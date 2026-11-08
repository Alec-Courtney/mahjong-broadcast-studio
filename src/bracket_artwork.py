"""Presentation of a bracket, separate from its scheduling/advancement rules."""
from event_schedule import node_matches, signature, resolve_sources, node_results, source_label


def render_bracket(state, event):
    from PySide6.QtCore import Qt,QRectF,QPointF
    from PySide6.QtGui import QPainter,QImage,QColor,QFont,QPen
    from panel_typography import draw_single_line
    bracket=event['bracket']; nodes=bracket['nodes']; roster={p['id']:p for p in state['broadcast']['roster']}
    rounds=sorted({n['round'] for n in nodes},reverse=True)
    width=max(1100, max((sum(n['round']==r for n in nodes) for r in rounds),default=1)*330+60)
    height=max(400,len(rounds)*270+90)
    if width*height>40000000:raise ValueError('晋级图过大，请分阶段编排赛程')
    image=QImage(width,height,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#0d201d'))
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def text(value,rect,size=16,color='#ffffff',bold=False):
        import artwork
        font=QFont(artwork.FONT);font.setPixelSize(size);font.setBold(bold);painter.setFont(font);painter.setPen(QColor(color))
        return draw_single_line(painter,str(value),rect,font,minimum=1,elide=False)
    text(event['name']+' · 淘汰晋级图',QRectF(28,15,width-56,60),28,bold=True)
    positions={}
    for level,round_number in enumerate(rounds):
        row=[n for n in nodes if n['round']==round_number]; gap=width/len(row)
        for i,node in enumerate(row):positions[node['id']]=QRectF(i*gap+(gap-300)/2,90+level*270,300,222)
    for node in nodes:
        target=positions[node['id']]
        for index,source in enumerate(node['sources']):
            if str(index) in node.get('source_overrides',{}):continue
            if 'node' in source:
                origin=positions[source['node']];painter.setPen(QPen(QColor('#42715e'),2))
                painter.drawLine(QPointF(origin.center().x(),origin.top()),QPointF(target.center().x(),target.bottom()))
    for node in nodes:
        rect=positions[node['id']];painter.setPen(QPen(QColor('#39816a' if node['status']=='complete' else '#3c5750'),1));painter.setBrush(QColor('#173e32' if node['status']=='complete' else '#182f2a'));painter.drawRoundedRect(rect,9,9)
        matches=node_matches(state,node['id']);text(node['name'],rect.adjusted(12,3,-12,-184),17,bold=True)
        label={'pending':'待开赛','running':'进行中','complete':'已确认晋级'}[node['status']]
        if node.get('result_signature') and node['result_signature']!=signature(state,node):label='成绩已更正 · 待核查'
        text(f'{label}  {len(matches)}/{node["planned_games"]} 场',QRectF(rect.x()+12,rect.y()+35,276,23),12,'#a8c3b5')
        resolved=resolve_sources(event,node); results={r['id']:r for r in node_results(state,event,node)}
        for seat,(identity,source) in enumerate(zip(resolved,node['sources'])):
            y=rect.y()+66+seat*36;is_winner=identity in node.get('winners',[])
            painter.fillRect(QRectF(rect.x()+10,y,280,32),QColor('#2b6445' if is_winner else '#213e35'))
            name=roster[identity]['name'] if identity in roster else source_label(event,source,roster)
            text(('✓ ' if is_winner else '')+name,QRectF(rect.x()+18,y,195,32),15)
            value=results.get(identity,{}).get('points');text(f'{value:+.1f}' if value is not None and matches else '—',QRectF(rect.x()+218,y,65,32),15,'#b2e2bb')
    if not nodes:text('请先在“淘汰赛程”编排四人桌',QRectF(35,150,width-70,50),22,'#a5c1b5')
    painter.end();return image
