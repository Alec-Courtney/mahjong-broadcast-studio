"""Replaceable tile-vision provider. No broadcast, Qt, or game-state imports.

Input is an OpenCV BGR frame. ROI uses input-frame pixels ``(x, y, w, h)``;
all returned boxes use absolute input-frame pixels ``(x1, y1, x2, y2)``.
Only ONNX is executed. Downloaded PT checkpoints are never deserialized.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import time
from typing import Any, Protocol, runtime_checkable

from app_paths import LOCAL_REFERENCE_RESOURCES
from recognition_runtime import recognition_available


MODEL_DIRECTORY = LOCAL_REFERENCE_RESOURCES / 'recognition'
MEDIUM_MODEL = MODEL_DIRECTORY / 'yolo11m_v2_best.onnx'
SMALL_MODEL = MODEL_DIRECTORY / 'yolo11s_v2_best.onnx'
NANO_MODEL = MODEL_DIRECTORY / 'yolo11n_v2_best.onnx'
LEGACY_SMALL_MODEL = MODEL_DIRECTORY / 'mahjong-yolos-best.onnx'
LEGACY_NANO_MODEL = MODEL_DIRECTORY / 'mahjong-yolon-best.onnx'
DEFAULT_MODEL = MEDIUM_MODEL


def recognition_model_path(size: str = 'medium') -> Path:
    """Prefer the requested size and use the available verified download as fallback."""
    choices = {
        'medium': (MEDIUM_MODEL, SMALL_MODEL, LEGACY_SMALL_MODEL, NANO_MODEL, LEGACY_NANO_MODEL),
        'small': (SMALL_MODEL, LEGACY_SMALL_MODEL, NANO_MODEL, LEGACY_NANO_MODEL, MEDIUM_MODEL),
        'nano': (NANO_MODEL, LEGACY_NANO_MODEL, SMALL_MODEL, LEGACY_SMALL_MODEL, MEDIUM_MODEL),
    }[size]
    return next((model for model in choices if model.is_file()), choices[0])


def normalize_class_name(name: str) -> str | None:
    """Unknown, face-down and unsupported classes must not become a tile."""
    value = str(name).strip().lower()
    return value if re.fullmatch(r'(?:[0-9][mps]|[1-7]z)', value) else None


@dataclass(frozen=True)
class TileDetection:
    tile: str | None
    confidence: float
    bbox: tuple[float, float, float, float]
    class_name: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RecognitionFrame:
    detections: tuple[TileDetection, ...]
    width: int
    height: int
    source: str = ''
    elapsed_ms: float = 0.0
    provider: str = ''
    captured_at: float = 0.0  # Process monotonic time; zero for unstamped external observations.

    @property
    def tiles(self) -> list[str]:
        return [d.tile for d in sorted(self.detections, key=lambda item: (item.bbox[0], item.bbox[1])) if d.tile]

    @property
    def unknown_count(self) -> int:
        return sum(d.tile is None for d in self.detections)

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class TileRecognizer(Protocol):
    """A provider can be replaced without any change to the operator desk."""
    provider_name: str

    def recognize(self, frame_bgr: Any, *, roi: tuple[int, int, int, int] | None = None) -> RecognitionFrame: ...


def _runtime():
    try:
        import cv2
        import numpy as np
        import onnxruntime as ort
    except (ImportError, OSError) as error:
        raise RuntimeError('牌面识别组件无法载入。请检查安装或运行 INSTALL-Automation.cmd。') from error
    return cv2, np, ort


def read_image(path: str | Path):
    """OpenCV imdecode supports Windows paths containing Chinese characters."""
    cv2, np, _ = _runtime()
    target = Path(path)
    frame = cv2.imdecode(np.frombuffer(target.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('无法读取图片')
    return frame


def write_image(path: str | Path, frame_bgr) -> None:
    cv2, _, _ = _runtime()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(target.suffix or '.png', frame_bgr)
    if not success:
        raise ValueError('无法保存识别预览')
    target.write_bytes(encoded.tobytes())


def _nms(boxes, scores, threshold: float):
    """Class-agnostic suppression: one physical face cannot be two classes."""
    import numpy as np
    order = np.argsort(-scores)
    selected = []
    while order.size:
        index = int(order[0])
        selected.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        left = np.maximum(boxes[index, :2], boxes[rest, :2])
        right = np.minimum(boxes[index, 2:], boxes[rest, 2:])
        intersection = np.prod(np.maximum(right - left, 0), axis=1)
        own_area = np.prod(np.maximum(boxes[index, 2:] - boxes[index, :2], 0))
        other_area = np.prod(np.maximum(boxes[rest, 2:] - boxes[rest, :2], 0), axis=1)
        overlap = intersection / np.maximum(own_area + other_area - intersection, 1e-9)
        order = rest[overlap <= threshold]
    return selected


class OnnxTileRecognizer:
    """CPU YOLO11 adapter with letterbox, metadata class mapping and NMS."""
    provider_name = 'nikmomo/Mahjong-YOLO · ONNX CPU'

    def __init__(self, model_path: str | Path | None = None, *, confidence: float = .55,
                 iou: float = .45, threads: int = 2):
        if not 0 < confidence <= 1 or not 0 < iou <= 1:
            raise ValueError('识别阈值必须在 0 到 1 之间')
        self.model_path = Path(model_path) if model_path is not None else recognition_model_path()
        if not self.model_path.is_file():
            raise FileNotFoundError('未找到本机 ONNX 牌面识别模型。请运行 INSTALL-Automation.cmd 下载模型。')
        cv2, np, ort = _runtime()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.confidence, self.iou = float(confidence), float(iou)
        input_spec = self.session.get_inputs()[0]
        if input_spec.type != 'tensor(float)' or len(input_spec.shape) != 4:
            raise ValueError('当前识别器只支持浮点 NCHW YOLO ONNX 模型')
        self.input_name = input_spec.name
        self.input_height = input_spec.shape[2] if isinstance(input_spec.shape[2], int) else 640
        self.input_width = input_spec.shape[3] if isinstance(input_spec.shape[3], int) else 640
        metadata = self.session.get_modelmeta().custom_metadata_map
        try:
            mapping = ast.literal_eval(metadata['names'])
            if isinstance(mapping, list):
                mapping = dict(enumerate(mapping))
            if not isinstance(mapping, dict) or not mapping or set(mapping) != set(range(len(mapping))):
                raise ValueError('class map')
            self.class_names = tuple(str(mapping[i]) for i in range(len(mapping)))
        except (KeyError, ValueError, SyntaxError, TypeError) as error:
            raise ValueError('模型缺少可验证的牌面分类名称') from error
        self.metadata = dict(metadata)

    def recognize(self, frame_bgr, *, roi: tuple[int, int, int, int] | None = None) -> RecognitionFrame:
        cv2, np, _ = _runtime()
        started = time.perf_counter()
        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.dtype != np.uint8 or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            raise ValueError('识别输入必须是 8 位 BGR 图像')
        height, width = frame_bgr.shape[:2]
        if min(height, width) <= 0:
            raise ValueError('识别图像不能为空')
        offset_x = offset_y = 0
        frame = frame_bgr
        if roi is not None:
            if len(roi) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in roi):
                raise ValueError('识别区域需要整数 x、y、宽、高')
            x, y, w, h = roi
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
                raise ValueError('识别区域超出图片范围')
            offset_x, offset_y = x, y
            frame = frame_bgr[y:y + h, x:x + w]
        crop_height, crop_width = frame.shape[:2]
        scale = min(self.input_width / crop_width, self.input_height / crop_height)
        scaled_width, scaled_height = round(crop_width * scale), round(crop_height * scale)
        resized = cv2.resize(frame, (scaled_width, scaled_height), interpolation=cv2.INTER_LINEAR)
        pad_x = (self.input_width - scaled_width) // 2
        pad_y = (self.input_height - scaled_height) // 2
        padded = np.full((self.input_height, self.input_width, 3), 114, dtype=np.uint8)
        padded[pad_y:pad_y + scaled_height, pad_x:pad_x + scaled_width] = resized
        tensor = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]
        if output.ndim != 3 or output.shape[0] != 1:
            raise ValueError('不支持的 YOLO 输出维度')
        prediction = output[0]
        feature_count = 4 + len(self.class_names)
        if prediction.shape[0] == feature_count:
            prediction = prediction.T
        elif prediction.shape[1] != feature_count:
            raise ValueError('YOLO 输出与模型分类名称数量不匹配')
        class_ids = np.argmax(prediction[:, 4:], axis=1)
        scores = prediction[np.arange(len(prediction)), 4 + class_ids]
        keep = (scores >= self.confidence) & np.isfinite(prediction).all(axis=1)
        prediction, scores, class_ids = prediction[keep], scores[keep], class_ids[keep]
        boxes = prediction[:, :4].copy()
        if len(boxes):
            boxes[:, :2] -= prediction[:, 2:4] / 2
            boxes[:, 2:4] += boxes[:, :2]
            boxes[:, (0, 2)] = np.clip((boxes[:, (0, 2)] - pad_x) / scale, 0, crop_width) + offset_x
            boxes[:, (1, 3)] = np.clip((boxes[:, (1, 3)] - pad_y) / scale, 0, crop_height) + offset_y
        detections = []
        for index in _nms(boxes, scores, self.iou):
            box = tuple(float(value) for value in boxes[index])
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            name = self.class_names[int(class_ids[index])]
            detections.append(TileDetection(normalize_class_name(name), float(scores[index]), box, name))
        detections.sort(key=lambda item: (item.bbox[0], item.bbox[1]))
        return RecognitionFrame(tuple(detections), width, height, str(self.model_path.name),
                                (time.perf_counter() - started) * 1000, self.provider_name)


class VideoSource:
    """Local recording, camera or named Spout source; close on the same worker."""
    def __init__(self, source: str | Path | int | dict):
        cv2, _, _ = _runtime()
        self.recoverable = False
        self.is_live = isinstance(source, (dict, int))
        if isinstance(source, bool):
            raise ValueError('无效的摄像头编号')
        if isinstance(source, dict) and source.get('kind') == 'spout':
            from spout_capture import SpoutCapture
            self.capture = SpoutCapture(source)
            self.source = dict(self.capture.source)
            self.recoverable = True
        elif isinstance(source, (dict, int)):
            from shared_camera import subscribe_camera
            if isinstance(source, int) and source < 0:
                raise ValueError('无效的摄像头编号')
            self.capture = subscribe_camera(source, cv2_module=cv2)
            self.source = self.capture.device.to_dict()
        else:
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError('视频文件不存在')
            self.source = str(path.resolve())
        if not isinstance(source, (dict, int)):
            self.capture = cv2.VideoCapture(self.source)
        try:
            if not self.capture.isOpened():
                raise RuntimeError('无法打开摄像头或视频')
            self.fps = float(self.capture.get(cv2.CAP_PROP_FPS) or 0)
            self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        except Exception:
            # The caller cannot close a VideoSource whose constructor failed.
            # Release native captures/subscriptions acquired before metadata.
            try:self.capture.release()
            except Exception:pass
            raise

    def read(self):
        result = self.capture.read()
        if result[0]:
            self.height, self.width = result[1].shape[:2] if hasattr(result[1], 'shape') else (self.height, self.width)
        return result

    @property
    def status(self):
        return getattr(self.capture, 'status', '')

    def close(self):
        self.capture.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def annotate(frame_bgr, result: RecognitionFrame):
    """Return a copy for previews; never change the original evidence frame."""
    cv2, _, _ = _runtime()
    preview = frame_bgr.copy()
    for detection in result.detections:
        x1, y1, x2, y2 = (round(v) for v in detection.bbox)
        color = (110, 225, 100) if detection.tile else (60, 160, 255)
        cv2.rectangle(preview, (x1, y1), (x2, y2), color, 2)
        title = f'{detection.tile or "UNKNOWN"} {detection.confidence:.2f}'
        cv2.putText(preview, title, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .55, (10, 10, 10), 3, cv2.LINE_AA)
        cv2.putText(preview, title, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 1, cv2.LINE_AA)
    return preview
