"""Named event-owned sidebar views, containing presentation settings only."""
from __future__ import annotations

import copy
import math
import re
import uuid

from sidebar_paging import is_team_event


SIDEBAR_SETTING_KEYS = frozenset(('sidebar_mode','sidebar_scope','sidebar_page',
    'sidebar_page_size','sidebar_auto_page','sidebar_page_interval','qualification_layout'))


def default_sidebar_presets():
    def entry(identity,title,mode='individual',scope='all',automatic=False):
        return dict(id=identity,title=title,settings=dict(sidebar_mode=mode,sidebar_scope=scope,
            sidebar_page=0,sidebar_page_size=10,sidebar_auto_page=automatic,
            sidebar_page_interval=10,qualification_layout='four'))
    return [entry('personal-home','个人首页'),entry('personal-auto','个人自动轮播',automatic=True),
            entry('current-four','当前四席',scope='current'),entry('team-ranking','团队排名',mode='team',automatic=True)]


def _integer(value,low,high,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or int(value)!=value or not low<=value<=high:
        raise ValueError(label+'超出有效范围')


def validate_sidebar_presets(values):
    """Reject foreign keys, ambiguous IDs and unsafe native-window geometry."""
    if not isinstance(values,list) or len(values)>20:raise ValueError('侧栏快捷方案最多20个')
    identities=set()
    for preset in values:
        if not isinstance(preset,dict) or set(preset)-{'id','title','settings','geometry'}:raise ValueError('侧栏快捷方案字段无效')
        identity=preset.get('id');title=preset.get('title');settings=preset.get('settings')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',identity) or identity in identities:
            raise ValueError('侧栏快捷方案编号无效或重复')
        identities.add(identity)
        if not isinstance(title,str) or not title.strip() or len(title)>80 or any(char in title for char in '\r\n'):
            raise ValueError('侧栏快捷方案名称须为1至80字单行文字')
        if not isinstance(settings,dict) or set(settings)!=SIDEBAR_SETTING_KEYS:raise ValueError('侧栏快捷方案只能保存侧栏显示设置')
        if settings['sidebar_mode'] not in ('qualification','individual','team'):raise ValueError('侧栏快捷方案内容无效')
        if settings['sidebar_scope'] not in ('all','current'):raise ValueError('侧栏快捷方案范围无效')
        if settings['qualification_layout'] not in ('four','sidebar'):raise ValueError('侧栏快捷方案版式无效')
        if not isinstance(settings['sidebar_auto_page'],bool):raise ValueError('侧栏快捷方案自动切页开关无效')
        _integer(settings['sidebar_page'],0,100000,'侧栏页码')
        _integer(settings['sidebar_page_size'],2,10,'侧栏每页数量')
        _integer(settings['sidebar_page_interval'],5,60,'侧栏换页间隔')
        if 'geometry' in preset:
            geometry=preset['geometry']
            if not isinstance(geometry,list) or len(geometry)!=4:raise ValueError('侧栏快捷方案窗口位置无效')
            for value in geometry[:2]:_integer(value,-20000,20000,'侧栏窗口坐标')
            _integer(geometry[2],120,4000,'侧栏窗口宽度')
            _integer(geometry[3],30,3000,'侧栏窗口高度')
    return copy.deepcopy(values)


def sidebar_presets(model):
    config=model.state.get('broadcast',{}).get('config',{})
    return copy.deepcopy(config.get('sidebar_presets',default_sidebar_presets()))


def preset_available(model,preset):
    settings=preset.get('settings',{}) if isinstance(preset,dict) else {}
    return bool(settings and (settings.get('sidebar_mode')!='team' or is_team_event(model)))


def preset_target(preset):
    settings=preset['settings']
    return 'four_conditions' if settings['sidebar_mode']=='qualification' and settings['qualification_layout']=='four' else 'qualification'


def preset_settings(preset):
    settings=copy.deepcopy(preset['settings'])
    if preset_target(preset)=='four_conditions':return {'qualification_layout':'four'}
    if settings['sidebar_mode'] in ('individual','team'):settings.pop('qualification_layout',None)
    return settings


def make_current_preset(title,manager):
    config=manager.model.state['broadcast']['config']
    defaults=default_sidebar_presets()[0]['settings']
    settings={key:copy.deepcopy(config.get(key,defaults[key])) for key in SIDEBAR_SETTING_KEYS}
    if settings['sidebar_mode'] in ('individual','team'):settings['sidebar_page']=manager.current_sidebar_page()
    window=manager.windows['qualification']
    result=dict(id=uuid.uuid4().hex,title=title.strip() if isinstance(title,str) else title,settings=settings,
                geometry=[window.x(),window.y(),window.width(),window.height()])
    if manager.enabled.get('four_conditions',False):
        settings.update(sidebar_mode='qualification',qualification_layout='four')
        result.pop('geometry',None)
    return validate_sidebar_presets([result])[0]
