# 内置演示数据

通过主控的“赛事 → 演示”载入，支持恢复测试前会话。场景清单与用途见 `catalog.json`。

`team_complete` 和 `individual_complete` 使用 M.League 官网名册与肖像，赛果由引擎模拟。其他内置场景使用绘制的演示头像。`m_tournament` 复现 2025 年 6 月 2 日公开的积分局面。 [名册与肖像来源](roster.json) · [演示导入说明](../../demo-suite/README.md)

`match_finished`由真实计分引擎逐局执行，12局包含2次流局。`final_edge`覆盖三家并列第一、负分及终局1000供托按400/300/300分配后的冻结名次。`yakuman_single`和`yakuman_multiple`分别覆盖32000与64000支付。

程序接口 `demo_data.load_demo_state(id)` 返回独立数据对象；`demo_data.build_demo_model(id)`使用临时目录，不读取现场会话。运行 `python src/demo_data.py`可重建场景。全部JSON使用稳定演示ID，图片引用在载入时解析为当前目录路径。

在项目根目录运行 `python src/complete_demo.py --portable` 可重建内置积分赛及三份可独立导入的赛事 JSON。
