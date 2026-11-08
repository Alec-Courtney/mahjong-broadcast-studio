"""Pure tile, scoring-display and yaku values shared by UI and domain code.

No model, persistence or Qt dependencies; model.py re-exports these names for
existing scripts.
"""
WINDS = ['东', '南', '西', '北']
COLORS = ['#ffffff', '#ffffff', '#ffffff', '#ffffff']
TEAM_COLORS = ['#377e72', '#316e9c', '#aa3e4b', '#6a5b99']
TILES = [f'{n}{s}' for s in 'mps' for n in range(1,10)] + [f'{n}z' for n in range(1,8)] + ['0m','0p','0s']

# label, closed han, open han. -1 represents a yakuman, not a numerical han.
_LEGACY_YAKU = [
 ('立直',1,0),('一発',1,0),('門前清自摸和',1,0),('断么九',1,1),('平和',1,0),('一盃口',1,0),
 ('自風',1,1),('場風',1,1),('白',1,1),('發',1,1),('中',1,1),('嶺上開花',1,1),
 ('槍槓',1,1),('海底摸月',1,1),('河底撈魚',1,1),('ダブル立直',2,0),
 ('七対子',2,0),('対々和',2,2),('三暗刻',2,2),('三色同順',2,1),('一気通貫',2,1),
 ('混全帯么九',2,1),('三色同刻',2,2),('三槓子',2,2),('小三元',2,2),('混老頭',2,2),
 ('混一色',3,2),('純全帯么九',3,2),('二盃口',3,0),('清一色',6,5),
 ('国士無双',-1,-1),('四暗刻',-1,0),('大三元',-1,-1),('小四喜',-1,-1),('大四喜',-1,-1),
 ('字一色',-1,-1),('緑一色',-1,-1),('清老頭',-1,-1),('九蓮宝燈',-1,0),('四槓子',-1,-1),('天和',-1,0),('地和',-1,0)
]

_CHINESE_PHRASES = {
 'ダブル立直':'两立直','門前清自摸和':'门前清自摸和','嶺上開花':'岭上开花',
 '河底撈魚':'河底捞鱼','一気通貫':'一气通贯','対々和':'对对和',
 '七対子':'七对子','一盃口':'一杯口','二盃口':'二杯口','ドラ':'宝牌',
 '混全帯么九':'混全带幺九','純全帯么九':'纯全带幺九',
 'リーチ':'立直','テンパイ':'听牌','待ち':'待牌','ロン':'荣和','ツモ':'自摸',
}
_CHINESE_CHARS = str.maketrans('東發門風場槍槓順帯頭純無緑蓮燈満貫発対盃',
                              '东发门风场抢杠顺带头纯无绿莲灯满贯发对杯')

def simplified(value):
 """Localize built-in labels, never apply this to a user profile name."""
 for old,new in _CHINESE_PHRASES.items():value=value.replace(old,new)
 return value.translate(_CHINESE_CHARS)

YAKU = [(simplified(name),closed,opened) for name,closed,opened in _LEGACY_YAKU]
YAKU_ALIASES = {name:simplified(name) for name,_,_ in _LEGACY_YAKU}
DEFAULT_NAMES = ['选手一','选手二','选手三','选手四']
LEGACY_DEFAULT_NAMES = ['青木 澪','佐野 航','森川 奏','高橋 凛']

def normalize_waits(tiles):
 out=[]
 for tile in tiles:
  if tile not in TILES:raise ValueError('待牌格式无效')
  tile='5'+tile[1] if tile[0]=='0' else tile
  if tile not in out:out.append(tile)
 return sorted(out,key=tile_sort_key)

def tile_sort_key(tile):
 return ('mpsz'.index(tile[1]),5 if tile[0]=='0' else int(tile[0]))

def display_score(state,index,mode='immediate'):
 """Deferred broadcast deposits never change the engine's conserved ledger."""
 player=state['players'][index]
 return player['score']+(1000 if mode=='deferred' and state['phase']=='playing' and player['riichi'] else 0)

def payment_lines(before,after,mode='immediate'):
 totals=[display_score(after,i,mode)-display_score(before,i,mode) for i in range(4)]
 if mode!='deferred' or after.get('phase')=='playing':return [(v,0) for v in totals]
 def main(state):
  result=state.get('lastResult') if state.get('phase')!='playing' else None
  if not result:return [0]*4
  awards=[sum(a['amount'] for a in result.get('potAwards',[]) if a['player']==i) for i in range(4)]
  return [v-awards[i] for i,v in enumerate(result['deltas'])]
 old,new=main(before),main(after)
 return [(new[i]-old[i],totals[i]-(new[i]-old[i])) for i in range(4)]

def tile_name(t):
 n,s=int(t[0]),t[1]
 return WINDS[n-1] if s=='z' and n<=4 else (['白','发','中'][n-5] if s=='z' else ('赤五' if n==0 else '一二三四五六七八九'[n-1])+{'m':'万','p':'筒','s':'索'}[s])

def parse_tiles(text):
 import re
 text=re.sub(r'[\s,，]+','',text.lower())
 if not text:return []
 if not re.fullmatch(r'(?:[0-9]+[mps]|[1-7]+z)+',text):raise ValueError('牌面格式无效')
 return [n+s for ns,s in re.findall(r'([0-9]+)([mpsz])',text) for n in ns]

def compact(tiles):
 return ''.join(''.join(t[0] for t in tiles if t[1]==s)+s for s in 'mpsz' if any(t[1]==s for t in tiles))

def yaku_total(names,opened=False,bonus=0,manual=False):
 catalog={x[0]:x for x in YAKU};selected=[catalog[simplified(n)] for n in names if simplified(n) in catalog]
 values=[row[2] if opened and (row[2]!=0 or not manual) else row[1] for row in selected]
 if any(v==0 for v in values):raise ValueError('所选役种与门前状态不符')
 yakuman=sum(v==-1 for v in values)
 return (0,yakuman) if yakuman else (sum(values)+bonus,0)
