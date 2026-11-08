# 识别准确率复核资料

四张 ABEMA 报道原图用于本机复核，存放在 `reference/local-only/tests/fixtures/recognition/accuracy/`。此处保留公开来源、SHA-256、人工标注、原始预测及报告。[图片来源记录](sources.json)

这是四张图片的小样本验证：49 个可辨认牌面正确检出 43 个，预测精确率 43/44，完整手牌全对 1/3；不能推断硬件摄像头、连续录像或训练集外的通用准确率。

`ground_truth.json` 记录人工标注方法和修订，四个 `*-result.json` 是原始输出，`report.json` 与 `guard-evaluation.json` 分别记录识别统计及稳定帧门槛行为。
