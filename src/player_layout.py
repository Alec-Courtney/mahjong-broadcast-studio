"""Broadcast geometry shared by live cards, rankings and score animation."""
from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtGui import QTransform

ML_BODY_SCALE = 184/196


def _design_x(display_x):
    """Place content in the wider card without stretching its glyphs/photos."""
    return 208+(display_x-208)/ML_BODY_SCALE


@dataclass(frozen=True)
class PlayerLayout:
    canvas: tuple[int, int] = (438, 348)
    left: float = _design_x(14)
    top: int = 133
    photo_width: int = 132
    photo_height: int = 196
    card_x: float = _design_x(134)
    card_width: float = 268/ML_BODY_SCALE
    score_y: int = 257
    score_height: int = 72
    status_y: int = 133
    status_height: int = 51
    dealer_y: int = 334

    def body_transform(self):
        """Compact ML artwork once without distorting glyphs or photographs."""
        transform = QTransform()
        transform.translate(208, 346)
        transform.scale(ML_BODY_SCALE, ML_BODY_SCALE)
        transform.translate(-208, -338)
        return transform

    def body_bounds(self):
        """ML card footprint used to frame both styles in the capture canvas."""
        return self.body_transform().mapRect(QRectF(
            self.left, self.top, self.card_x + self.card_width - self.left, self.photo_height))

    def envelope(self, theme='mleague'):
        if theme == 'saikouisen':
            return QRectF(4, 154, 406, 184)
        return QRectF(self.left-10, 123,
                      self.card_x+self.card_width+8-(self.left-10), 215)

    def score_bounds(self):
        return QRectF(self.card_x + 12, self.score_y + 6,
                      self.card_width - 25, self.score_height - 12)

    def payment_rows(self, show_melds, has_deposit, has_ordinary=True, *, settlement=False, legacy=False):
        """Return the (ordinary points, riichi deposit) vertical centers.

        The deposit always owns the upper row, including a lone deposit.
        An absent ordinary payment leaves its lower row empty.
        ML hand settlements use the cleared identity region. Live declaration
        payments retain the space above the card, where names remain visible.
        """
        if settlement:
            return 236, 188
        lower = (96 if show_melds else 164 if legacy else self.top) - 23
        return lower, lower - 48


PLAYER_LAYOUT = PlayerLayout()
PLAYER_CAPTURE_SIZE = (1920, 370)
PLAYER_CAPTURE_BOTTOM = 22
DEALER_COLOR = '#e62332'


def restore_player_geometries(saved, legacy_defaults, defaults, version):
    """Restore four seats together; repair the v1 per-seat default migration."""
    keys=[f'player{i}' for i in range(4)]
    rounded=lambda geometry:[round(value) for value in geometry]
    current={key:rounded(defaults[key]) for key in keys}
    if not any(key in saved for key in keys):return current
    if version<1:
        previous={key:rounded(saved.get(key,legacy_defaults[key])) for key in keys}
        # A customized member preserves the complete old group, including the
        # unedited seats. Never replace only its default-looking neighbours.
        if all(previous[key]==rounded(legacy_defaults[key]) for key in keys):return current
        return previous
    frames={key:rounded(saved.get(key,defaults[key])) for key in keys}
    if version!=1:return frames
    matching=[i for i,key in enumerate(keys) if frames[key]==current[key]]
    customized=[i for i in range(4) if i not in matching]
    def same_row(indices):
        first=frames[keys[indices[0]]]
        return all(abs(frames[keys[i]][1]-first[1])<=8 and
                   abs(frames[keys[i]][2]-first[2])<=2 and
                   abs(frames[keys[i]][3]-first[3])<=2 for i in indices)
    if same_row(range(4)):return frames
    # Three new defaults plus one old seat: retain the new row. Otherwise keep
    # the coherent customized majority and restore the migrated seats to it.
    rows=[[i for i in range(4) if same_row([pivot,i])] for pivot in range(4)]
    dominant=max(rows,key=len)
    # The broken group may already have been dragged as a whole. Its healthy
    # three seats still form one row even after a common translation.
    if len(dominant)>=3:anchors=dominant
    elif matching and customized:anchors=matching if len(matching)>=3 else customized
    else:return frames
    if len(anchors)<2 or not same_row(anchors):return frames
    first,last=anchors[0],anchors[-1]
    step=(frames[keys[last]][0]-frames[keys[first]][0])/(last-first)
    if step<=0:return frames
    origin=frames[keys[first]][0]-first*step
    if any(abs(frames[keys[i]][0]-(origin+i*step))>8 for i in anchors):return frames
    middle=lambda column:sorted(frames[keys[i]][column] for i in anchors)[len(anchors)//2]
    for i in range(4):
        if i not in anchors:frames[keys[i]]=[round(origin+i*step),middle(1),middle(2),middle(3)]
    return frames
