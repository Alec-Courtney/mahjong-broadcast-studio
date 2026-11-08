"""Shared native-widget assertions; this module generates no preview files."""
from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea
from model import YAKU


def settle(app):
    for _ in range(3):app.processEvents();QTest.qWait(25)


def visibility(widget,window):
    if not widget.isVisible():return dict(visible=False,fraction=0.)
    original=QRect(widget.rect());remaining=QRect(original);child=widget
    while child is not window:
        parent=child.parentWidget()
        if parent is None:return dict(visible=False,fraction=0.)
        remaining.translate(child.pos());remaining=remaining.intersected(parent.rect());child=parent
    area=max(1,original.width()*original.height());shown=max(0,remaining.width())*max(0,remaining.height())
    point=widget.mapTo(window,QPoint(0,0))
    return dict(visible=shown>=area*.99,fraction=round(shown/area,4),
                rect=[point.x(),point.y(),original.width(),original.height()])


def controls(window):
    result={f'player_score_{i+1}':row[2] for i,row in enumerate(window.player_widgets)}
    result.update({key:getattr(window,key) for key in (
        'round','dealer','honba','pot','wait_enabled','remaining','submit_wait_button',
        'meld_kind','meld_from','add_meld_button','winner','loser','fu','han','settle_button')})
    for name,_,_ in YAKU[:30]:result['yaku:'+name]=window.yaku_buttons[name]
    for name,widget in zip(('bonus_dora','bonus_red','bonus_ura'),window.bonuses):result[name]=widget
    for code in ('1m','0s','7z'):result['tile:'+code]=window.tile_buttons[code]
    first=window.dora_row.itemAt(0)
    if first and first.widget():result['dora_indicator']=first.widget()
    return result


def reveal(widget,app):
    parent=widget.parentWidget()
    while parent is not None:
        if isinstance(parent,QScrollArea):parent.ensureWidgetVisible(widget,0,4)
        parent=parent.parentWidget()
    settle(app)
    # Qt may reveal only a spin box's focusProxy (its inner editor), leaving
    # the arrows/border clipped. Measure and reveal the complete control too.
    parent=widget.parentWidget()
    while parent is not None:
        if isinstance(parent,QScrollArea):
            viewport=parent.viewport();area=QRect(widget.rect())
            area.moveTopLeft(widget.mapTo(viewport,QPoint(0,0)))
            for bar,low,high,limit in (
                (parent.horizontalScrollBar(),area.left(),area.right(),viewport.width()-1),
                (parent.verticalScrollBar(),area.top(),area.bottom(),viewport.height()-1)):
                if low<0:bar.setValue(bar.value()+low)
                elif high>limit:bar.setValue(bar.value()+high-limit)
        parent=parent.parentWidget()
    app.processEvents()
