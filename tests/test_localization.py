import os,sys,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt,QLocale
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QComboBox,QLineEdit,QTableWidget,QTableWidgetItem,QPushButton,QSpinBox
from i18n import set_language,get_language,tr,localize_widgets,LanguageController
APP=QApplication.instance() or QApplication([])

class LocalizationTests(unittest.TestCase):
 def setUp(self):set_language('zh_CN')
 def tearDown(self):set_language('zh_CN');APP.processEvents()
 def test_roundtrip_preserves_data_names_and_values(self):
  root=QWidget();layout=QVBoxLayout(root);label=QLabel('晋级条件');layout.addWidget(label)
  player=QLabel('立直');player.setProperty('i18nLiteral',True);layout.addWidget(player)
  combo=QComboBox();combo.addItem('四进一',1);combo.addItem('四进三',3);combo.setCurrentIndex(1);layout.addWidget(combo)
  name=QLineEdit('姚思誉');name.setPlaceholderText('姓名');layout.addWidget(name)
  count=QSpinBox();count.setRange(0,100);count.setValue(3);count.setSuffix(' 张');layout.addWidget(count)
  table=QTableWidget(1,2);table.setHorizontalHeaderLabels(['姓名','放铳率']);table.setItem(0,0,QTableWidgetItem('牌手'));table.setItem(0,1,QTableWidgetItem('参赛'));layout.addWidget(table)
  set_language('ja_JP');localize_widgets(root)
  self.assertEqual(label.text(),'通過条件');self.assertEqual(player.text(),'立直');self.assertEqual(combo.currentText(),'4人中3人通過');self.assertEqual(combo.currentData(),3)
  self.assertEqual(name.text(),'姚思誉');self.assertEqual(name.placeholderText(),'氏名');self.assertEqual(count.value(),3);self.assertEqual(count.suffix(),' 枚')
  self.assertEqual(table.horizontalHeaderItem(1).text(),'放銃率');self.assertEqual(table.item(0,1).text(),'参赛')
  localize_widgets(root);set_language('zh_CN');localize_widgets(root)
  self.assertEqual(label.text(),'晋级条件');self.assertEqual(combo.itemText(0),'四进一');self.assertEqual(name.placeholderText(),'姓名');self.assertEqual(count.suffix(),' 张');self.assertEqual(table.horizontalHeaderItem(1).text(),'放铳率')
  root.close();root.deleteLater();APP.processEvents()
 def test_dynamic_values_refresh_and_literal_strings(self):
  self.assertEqual(tr('姚思誉',literal=True),'姚思誉');set_language('ja_JP')
  self.assertEqual(tr('前3名晋级 · 本局结束试算'),'上位3名通過 · 本局終了時の試算')
  for slots in (1,2,3):
   self.assertEqual(tr(f'前{slots}名晋级'),f'上位{slots}名通過');self.assertEqual(tr(f'前{slots}队晋级'),f'上位{slots}チーム通過')
  self.assertEqual(tr('自摸 500 / 1,000 等12档'),'ツモ 500 / 1,000 ほか計12通り')
  self.assertEqual(tr('南 4 局'),'南 4 局');self.assertEqual(tr('东 1局'),'東 1 局')
  self.assertEqual(tr('合计'),'合計');self.assertEqual(tr('500 ALL'),'500オール');self.assertEqual(tr('新半庄起点'),'新しい半荘の持ち点');
  self.assertNotIn('本场',tr('按当前积分逐档试算；支付档不含本场，计算已加本场与供托。 / 高打点未必更有利；不同放铳者分别计算，不把最低通过档写成“以上”。'))
  self.assertEqual(tr('宝牌 3'),'ドラ 3');self.assertEqual(tr('立直',literal=True),'立直')
  label=QLabel('已记录 1 局  ·  流局 0 局');localize_widgets(label);self.assertEqual(label.text(),'記録済み 1局 · 流局 0局')
  label.setText('已记录 2 局  ·  流局 1 局');localize_widgets(label);self.assertEqual(label.text(),'記録済み 2局 · 流局 1局')
  set_language('zh_CN');localize_widgets(label);self.assertEqual(label.text(),'已记录 2 局  ·  流局 1 局');label.close();label.deleteLater()
 def test_controller_switches_existing_and_new_dialogs(self):
  root=QWidget();layout=QVBoxLayout(root);button=QPushButton('赛事');layout.addWidget(button)
  controller=LanguageController(APP);controller.track(root)
  set_language('ja_JP');self.assertEqual(button.text(),'大会');self.assertEqual(QLocale().language(),QLocale.Language.Japanese)
  new=QLabel('保存');new.setParent(root);new.show();APP.processEvents();controller.refresh();self.assertEqual(new.text(),'保存')
  other=QLabel('放铳');layout.addWidget(other);APP.processEvents();self.assertEqual(other.text(),'放銃')
  set_language('zh_CN');self.assertEqual(button.text(),'赛事');self.assertEqual(other.text(),'放铳')
  controller.dispose();set_language('ja_JP');self.assertEqual(button.text(),'赛事');controller.deleteLater();root.close();root.deleteLater();APP.processEvents()
 def test_builtin_terms_have_japanese_and_custom_is_literal(self):
  from glossary import CATEGORIES,GLOSSARY
  set_language('ja_JP')
  for entry in GLOSSARY:
   self.assertEqual(tr(entry['zh_title']),entry['ja_title'],entry['id'])
   self.assertEqual(tr(entry['zh_body']),entry['ja_body'],entry['id'])
  for entry in CATEGORIES:self.assertEqual(tr(entry['zh_title']),entry['ja_title'])
  for text in ('全部分类','术语分类','实战术语','选择实战术语','自定义'):
   self.assertNotEqual(tr(text),text)
  self.assertEqual(tr('团队自己的说明',literal=True),'团队自己的说明')

 def test_demo_titles_descriptions_and_messages_have_japanese(self):
  from demo_data import DEMO_SCENARIOS,DEMO_DESCRIPTIONS
  set_language('ja_JP')
  for scenario,label in DEMO_SCENARIOS:
   self.assertNotEqual(tr(label),label,scenario)
   self.assertNotEqual(tr(DEMO_DESCRIPTIONS[scenario]),DEMO_DESCRIPTIONS[scenario],scenario)
   self.assertEqual(tr('当前演示：'+label),'現在のデモ：'+tr(label))
   self.assertEqual(tr('演示 · '+label),'デモ · '+tr(label))
  self.assertEqual(tr('演示数据'),'デモデータ')
  self.assertEqual(tr('没有可恢复的测试前数据'),'復元できるテスト前のデータがありません')
  self.assertEqual(tr('青木 凛',literal=True),'青木 凛')

 def test_bottom_modes_keep_stable_values_when_switching_language(self):
  combo=QComboBox()
  for label,value in [('选手信息','players'),('个人积分','individual'),('队伍积分','team')]:combo.addItem(label,value)
  combo.setCurrentIndex(2);set_language('ja_JP');localize_widgets(combo)
  self.assertEqual(combo.currentData(),'team');self.assertEqual(combo.currentText(),'チームポイント')
  self.assertEqual(combo.itemText(0),'選手情報');self.assertEqual(tr('底部显示'),'下部表示')
  self.assertEqual(tr('底部积分'),'下部ポイント');self.assertEqual(tr('底部显示模式无效'),'下部表示モードが無効です')
  set_language('zh_CN');localize_widgets(combo)
  self.assertEqual(combo.currentText(),'队伍积分');self.assertEqual(combo.currentData(),'team')
  combo.close();combo.deleteLater();APP.processEvents()

 def test_sidebar_and_center_action_remain_functional_in_both_languages(self):
  root=QWidget();layout=QVBoxLayout(root);mode=QComboBox();mode.addItem('晋级条件','qualification');mode.addItem('队伍排名','team');layout.addWidget(mode)
  center=QPushButton('自动定位');layout.addWidget(center);clicked=[];center.clicked.connect(lambda:clicked.append(True));mode.setCurrentIndex(1)
  set_language('ja_JP');localize_widgets(root);center.click()
  self.assertEqual(mode.currentText(),'チーム順位');self.assertEqual(mode.currentData(),'team');self.assertEqual(center.text(),'自動配置');self.assertEqual(clicked,[True])
  self.assertEqual(tr('麻将UI · 队伍排名'),'麻雀UI · チーム順位');self.assertEqual(tr('侧栏显示模式无效'),'サイドバーの表示モードが無効です')
  self.assertEqual(tr('左侧信息'),'左側情報');self.assertEqual(tr('侧栏每页数量必须是整数'),'サイドバーの1ページ件数は整数で入力してください')
  set_language('zh_CN');localize_widgets(root);center.click()
  self.assertEqual(mode.currentText(),'队伍排名');self.assertEqual(center.text(),'自动定位');self.assertEqual(clicked,[True,True])
  root.close();root.deleteLater();APP.processEvents()

if __name__=='__main__':unittest.main()
