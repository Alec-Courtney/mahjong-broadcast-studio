from pathlib import Path
import os,sys
# Broadcast capture windows retain physical pixel dimensions across monitor DPI.
if sys.platform=='win32':os.environ['QT_ENABLE_HIGHDPI_SCALING']='0'
sys.path.insert(0,str(Path(__file__).parent/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtCore import QLocale,QTranslator,QLibraryInfo
from model import Model
from model import ROOT,APP_DIR
from overlays import OverlayManager
from console import Console
import artwork

def create_model(data_dir=None, ui_only=False):
 """A standalone desk has its own session and shares application preferences."""
 library_root=Path(data_dir or APP_DIR/'data')
 if ui_only:session_directory=library_root/'standalone'
 else:
  from event_store import resolve_active_directory
  session_directory=resolve_active_directory(library_root)
 model=Model(session_directory,
             asset_root=APP_DIR,preferences_root=library_root)
 model.ui_only=ui_only
 if not ui_only:
  from event_store import EventStore
  EventStore(model,root=library_root)
 return model

def main():
 import argparse
 parser=argparse.ArgumentParser(description='麻将转播控制台');parser.add_argument('--data-dir',type=Path,help='独立赛事库目录')
 parser.add_argument('--ui-only',action='store_true',help='仅使用导播 UI，保存独立工作区，不载入赛事库')
 options,qt_args=parser.parse_known_args()
 QLocale.setDefault(QLocale('zh_CN'));app=QApplication([sys.argv[0],*qt_args]);app.setApplicationName('麻将转播控制台');app.setOrganizationName('LocalStudio')
 translator=QTranslator(app)
 if translator.load('qtbase_zh_CN',QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):app.installTranslator(translator)
 if sys.platform=='win32':
  for name in ('msyh.ttc','msyhbd.ttc'):
   font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
   if font.exists():QFontDatabase.addApplicationFont(str(font))
 for name in ['NotoSansCJKsc-Regular.otf','NotoSansCJKjp-Regular.otf']:
  font=ROOT/'resources'/name
  if font.exists():QFontDatabase.addApplicationFont(str(font))
 families=QFontDatabase.families();artwork.FONT=next((f for f in ['Microsoft YaHei','Microsoft YaHei UI','Noto Sans CJK SC','Source Han Sans SC','SimHei','Noto Sans CJK JP'] if f in families),'Sans Serif');app.setFont(QFont(artwork.FONT,10))
 try:model=create_model(options.data_dir,options.ui_only)
 except Exception as error:
  from PySide6.QtWidgets import QMessageBox
  QMessageBox.critical(None,'无法打开工作区',str(error)+'\n原有文件已保留。');return 1
 demo_error = ''
 if model.event_store and options.data_dir is None:
  try:model.event_store.ensure_builtin_demo()
  except (OSError,ValueError) as error:demo_error='无法添加内置演示赛事。'+str(error)
 manager=OverlayManager(model);console=Console(model,manager);console.show()
 if demo_error:console.error(demo_error)
 result=app.exec()
 # Finish interrupted camera or recognition calls while Qt is still alive.
 from recognition_source_editor import wait_for_drained_workers
 wait_for_drained_workers()
 return result
if __name__=='__main__':sys.exit(main())
