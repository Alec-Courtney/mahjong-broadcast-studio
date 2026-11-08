local obs = obslua

-- 按“下家方向”排列；这是桌上固定位置，不随庄家轮转。
local cameras = {"机位A", "机位B", "机位C", "机位D"}
local hotkeys = {}
local script_settings = nil
local default_keys = {right = "OBS_KEY_RIGHT", left = "OBS_KEY_LEFT", up = "OBS_KEY_UP"}

local function log(message)
    obs.script_log(obs.LOG_WARNING, "日麻镜头：" .. message)
end

-- 成功时返回持有引用的 scene_source，由调用者释放。失败时不改任何可见性。
local function camera_items()
    local scene_source = obs.obs_frontend_get_current_scene()
    if scene_source == nil then return nil, nil, "没有当前场景" end
    local scene = obs.obs_scene_from_source(scene_source)
    if scene == nil then
        obs.obs_source_release(scene_source)
        return nil, nil, "当前来源不是场景"
    end
    local items, names = {}, {}
    for i = 1, 4 do
        local name, problem = cameras[i], nil
        if name == "" then
            problem = "机位 " .. i .. " 未配置来源名称"
        elseif names[name] then
            problem = "机位名称重复：" .. name .. "；四个机位必须使用不同来源"
        end
        if problem == nil then
            names[name] = true
            items[i] = obs.obs_scene_find_source_recursive(scene, name)
            if items[i] == nil then problem = "当前场景未找到来源：" .. name end
        end
        if problem ~= nil then
            obs.obs_source_release(scene_source)
            return nil, nil, problem
        end
    end
    return scene_source, items, nil
end

local function visible_camera(items)
    local current, count = nil, 0
    for i = 1, 4 do
        if obs.obs_sceneitem_visible(items[i]) then current, count = i, count + 1 end
    end
    return current, count
end

local function status_text()
    local scene_source, items, problem = camera_items()
    if problem ~= nil then return "配置检查：" .. problem end
    local current, count = visible_camera(items)
    obs.obs_source_release(scene_source)
    if count ~= 1 then
        return "当前有 " .. count .. " 个机位显示。请先点击“显示机位 1”，或手动只显示一个机位。"
    end
    return "当前机位：" .. current .. "（" .. cameras[current] .. "）。可点击下面的测试按钮检查切换。"
end

local function refresh_status(props)
    if props ~= nil then
        local prop = obs.obs_properties_get(props, "camera_status")
        if prop ~= nil then obs.obs_property_set_description(prop, status_text()) end
    end
end

local function show_camera(items, target)
    -- 先显示目标，再隐藏其他镜头；不触碰其他叠加来源。
    obs.obs_sceneitem_set_visible(items[target], true)
    for i = 1, 4 do
        if i ~= target then obs.obs_sceneitem_set_visible(items[i], false) end
    end
end

local function switch_relative(offset)
    local scene_source, items, problem = camera_items()
    if problem ~= nil then log(problem); return end
    local current, count = visible_camera(items)
    if count ~= 1 then
        log("需要恰好一个机位显示，当前为 " .. count .. "。请先在脚本中点击“显示机位 1”，或手动只显示一个机位。")
    else
        show_camera(items, ((current - 1 + offset) % 4) + 1)
    end
    obs.obs_source_release(scene_source)
end

local function next_player(pressed)
    if pressed then switch_relative(1) end
end

local function previous_player(pressed)
    if pressed then switch_relative(-1) end
end

local function opposite_player(pressed)
    if pressed then switch_relative(2) end
end

local function load_default_key(id, key)
    local data = obs.obs_data_create_from_json('{"bindings":[{"key":"' .. key .. '"}]}')
    local bindings = obs.obs_data_get_array(data, "bindings")
    obs.obs_hotkey_load(id, bindings)
    obs.obs_data_array_release(bindings)
    obs.obs_data_release(data)
end

function script_description()
    return [[日麻直播：相对机位切换

→ 下家；← 上家；↑ 对家。首次添加脚本会绑定这三个方向键。
已保存的按键（包括主动清空）会保留；旧版无绑定时，请点击“绑定方向键”。
也可在 OBS 设置 → 快捷键中搜索“日麻镜头”，自行分配不冲突的按键。

按下家顺序填写四个不同来源的名称，四源必须在当前场景中。
先只显示一个机位，再切换；全关或多个显示时，可明确点击“显示机位 1”复位。
脚本只切换摄像头可见性。分组父项也须显示；不切换场景，不改选手或庄家。
方向键也可能被 OBS 列表/预览控件使用，建议关闭脚本窗口后测试；冲突时改用组合键。]]
end

function script_defaults(settings)
    for i = 1, 4 do obs.obs_data_set_default_string(settings, "camera_" .. i, cameras[i]) end
end

function script_update(settings)
    for i = 1, 4 do
        cameras[i] = obs.obs_data_get_string(settings, "camera_" .. i):match("^%s*(.-)%s*$")
    end
end

function script_properties()
    local props = obs.obs_properties_create()
    for i = 1, 4 do
        obs.obs_properties_add_text(props, "camera_" .. i, "机位 " .. i .. "（下家顺序）", obs.OBS_TEXT_DEFAULT)
    end
    obs.obs_properties_add_text(props, "camera_status", status_text(), obs.OBS_TEXT_INFO)
    obs.obs_properties_add_button(props, "check_cameras", "检查当前场景", function(p)
        log(status_text())
        refresh_status(p)
        return true
    end)
    obs.obs_properties_add_button(props, "restore_direction_keys", "绑定方向键（→ 下家 / ← 上家 / ↑ 对家）", function()
        for key, id in pairs(hotkeys) do load_default_key(id, default_keys[key]) end
        -- OBS 的“重载脚本”不会先调用 script_save；同步到脚本设置，立即重载也可保留。
        if script_settings ~= nil then script_save(script_settings) end
        log("已绑定方向键。可在 OBS 设置 → 快捷键中修改或清空；重载脚本保留已保存的按键。")
        return false
    end)
    obs.obs_properties_add_button(props, "start_camera_1", "显示机位 1（作为切换起点）", function(p)
        local scene_source, items, problem = camera_items()
        if problem ~= nil then
            log(problem)
        else
            show_camera(items, 1)
            obs.obs_source_release(scene_source)
        end
        refresh_status(p)
        return true
    end)
    obs.obs_properties_add_button(props, "test_next", "测试：下家 →", function(p)
        switch_relative(1)
        refresh_status(p)
        return true
    end)
    obs.obs_properties_add_button(props, "test_previous", "测试：上家 ←", function(p)
        switch_relative(-1)
        refresh_status(p)
        return true
    end)
    obs.obs_properties_add_button(props, "test_opposite", "测试：对家 ↑", function(p)
        switch_relative(2)
        refresh_status(p)
        return true
    end)
    return props
end

function script_load(settings)
    if script_settings ~= nil then obs.obs_data_release(script_settings) end
    obs.obs_data_addref(settings)
    script_settings = settings
    script_update(settings)
    hotkeys.right = obs.obs_hotkey_register_frontend("mahjong_camera_right", "日麻镜头：下家（右箭头）", next_player)
    hotkeys.left = obs.obs_hotkey_register_frontend("mahjong_camera_left", "日麻镜头：上家（左箭头）", previous_player)
    hotkeys.up = obs.obs_hotkey_register_frontend("mahjong_camera_up", "日麻镜头：对家（上箭头）", opposite_player)
    for key, id in pairs(hotkeys) do
        local setting = "hotkey_" .. key
        -- 空数组代表用户主动清空，不能在每次重载时偷偷重新绑定。
        if obs.obs_data_has_user_value(settings, setting) then
            local saved = obs.obs_data_get_array(settings, setting)
            obs.obs_hotkey_load(id, saved)
            if saved == nil or obs.obs_data_array_count(saved) == 0 then
                log("快捷键未绑定：" .. key .. "。请在脚本中点击“绑定方向键”，或在 OBS 设置 → 快捷键中分配。")
            end
            if saved ~= nil then obs.obs_data_array_release(saved) end
        else
            load_default_key(id, default_keys[key])
        end
    end
end

function script_save(settings)
    for key, id in pairs(hotkeys) do
        local saved = obs.obs_hotkey_save(id)
        obs.obs_data_set_array(settings, "hotkey_" .. key, saved)
        obs.obs_data_array_release(saved)
    end
end

function script_unload()
    obs.obs_hotkey_unregister(next_player)
    obs.obs_hotkey_unregister(previous_player)
    obs.obs_hotkey_unregister(opposite_player)
    hotkeys = {}
    if script_settings ~= nil then
        obs.obs_data_release(script_settings)
        script_settings = nil
    end
end
