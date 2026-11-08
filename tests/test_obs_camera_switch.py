"""Execute the real OBS Lua script against a small OBS API double.

Uses OBS's existing LuaJIT on Windows (or OBS_LUA_LIBRARY); never opens devices.
"""
import ctypes
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    os.environ.get("OBS_LUA_LIBRARY", ""),
    r"C:\Program Files (x86)\Steam\steamapps\common\OBS Studio\bin\64bit\lua51.dll",
    r"C:\Program Files\obs-studio\bin\64bit\lua51.dll",
]
LIBRARY = next((p for p in CANDIDATES if p and Path(p).is_file()), None)

MOCK = r"""
logs, handlers, loaded, unregistered = {}, {}, {}, {}
items = {
    A = {visible = true}, B = {visible = false},
    C = {visible = false}, D = {visible = false},
    overlay = {visible = true},
}
mutations, scene_releases, array_releases = 0, 0, 0
has_scene = true
settings = {camera_1 = "A", camera_2 = "B", camera_3 = "C", camera_4 = "D"}
data_refs = {[settings] = 1}
obslua = {
    LOG_WARNING = 2, OBS_TEXT_DEFAULT = 0, OBS_TEXT_INFO = 2,
    script_log = function(level, msg) table.insert(logs, msg) end,
    obs_frontend_get_current_scene = function() if has_scene then return items end end,
    obs_scene_from_source = function(source) return source end,
    obs_source_release = function() scene_releases = scene_releases + 1 end,
    obs_scene_find_source_recursive = function(scene, name) return scene[name] end,
    obs_sceneitem_visible = function(item) return item.visible end,
    obs_sceneitem_set_visible = function(item, visible)
        mutations = mutations + 1
        item.visible = visible
    end,
    obs_data_set_default_string = function(data, key, value)
        if data[key] == nil then data[key] = value end
    end,
    obs_data_get_string = function(data, key) return data[key] or "" end,
    obs_data_has_user_value = function(data, key) return data[key] ~= nil end,
    obs_data_get_array = function(data, key) return data[key] end,
    obs_data_set_array = function(data, key, value) data[key] = value end,
    obs_data_array_count = function(array) return #array end,
    obs_data_array_release = function(array)
        assert(array ~= nil, "cannot release nil array")
        array_releases = array_releases + 1
    end,
    obs_data_create_from_json = function(text)
        local key = text:match('"key":"([^"]+)"')
        assert(key, "invalid key JSON")
        local data = {bindings = {{key = key}}}
        data_refs[data] = 1
        return data
    end,
    obs_data_addref = function(data)
        assert(data_refs[data] and data_refs[data] > 0, "invalid data reference")
        data_refs[data] = data_refs[data] + 1
    end,
    obs_data_release = function(data)
        assert(data_refs[data] and data_refs[data] > 0, "double release")
        data_refs[data] = data_refs[data] - 1
    end,
    obs_hotkey_register_frontend = function(name, description, callback)
        handlers[name] = callback
        return name
    end,
    obs_hotkey_unregister = function(callback)
        table.insert(unregistered, callback)
    end,
    obs_hotkey_load = function(id, array) loaded[id] = array end,
    obs_hotkey_save = function(id) return loaded[id] or {} end,
    obs_properties_create = function() return {} end,
    obs_properties_add_text = function(props, key, description)
        props[key] = {description = description}
    end,
    obs_properties_add_button = function(props, key, label, callback)
        props[key] = {callback = callback}
    end,
    obs_properties_get = function(props, key) return props[key] end,
    obs_property_set_description = function(prop, text) prop.description = text end,
}
function visible(name)
    for key, value in pairs(items) do
        if key ~= "overlay" then assert(value.visible == (key == name), key) end
    end
    assert(items.overlay.visible, "overlay was changed")
end
"""


@unittest.skipUnless(LIBRARY, "An existing OBS LuaJIT library is required")
class OBSCameraSwitchLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(LIBRARY)
        cls.lib.luaL_newstate.restype = ctypes.c_void_p
        cls.lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lib.luaL_loadstring.restype = ctypes.c_int
        cls.lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        cls.lib.lua_pcall.restype = ctypes.c_int
        cls.lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        cls.lib.lua_tolstring.restype = ctypes.c_char_p
        cls.lib.lua_close.argtypes = [ctypes.c_void_p]

    def run_lua(self, scenario):
        state = self.lib.luaL_newstate()
        self.assertTrue(state)
        try:
            self.lib.luaL_openlibs(state)
            source = (ROOT / "mahjong_camera_switch.lua").read_text(encoding="utf-8-sig")
            code = (MOCK + "\n" + source + "\n" + scenario).encode("utf-8")
            status = self.lib.luaL_loadstring(state, code)
            if not status:
                status = self.lib.lua_pcall(state, 0, 0, 0)
            if status:
                message = self.lib.lua_tolstring(state, -1, None)
                self.fail(message.decode("utf-8", errors="replace") if message else f"Lua error {status}")
        finally:
            self.lib.lua_close(state)

    def test_first_install_binds_defaults_and_roundtrips(self):
        self.run_lua("""
            script_load(settings)
            assert(loaded.mahjong_camera_right[1].key == "OBS_KEY_RIGHT")
            assert(loaded.mahjong_camera_left[1].key == "OBS_KEY_LEFT")
            assert(loaded.mahjong_camera_up[1].key == "OBS_KEY_UP")
            script_save(settings)
            assert(settings.hotkey_right[1].key == "OBS_KEY_RIGHT")
            script_unload()
            assert(#unregistered == 3)
            script_load(settings)
            assert(loaded.mahjong_camera_right[1].key == "OBS_KEY_RIGHT")
        """)

    def test_saved_custom_and_explicit_empty_bindings_survive_reload(self):
        self.run_lua("""
            settings.hotkey_right = {{key = "OBS_KEY_F8"}}
            settings.hotkey_left = {}
            settings.hotkey_up = {}
            script_load(settings)
            assert(loaded.mahjong_camera_right[1].key == "OBS_KEY_F8")
            assert(#loaded.mahjong_camera_left == 0)
            assert(#loaded.mahjong_camera_up == 0)
            script_save(settings)
            script_unload()
            script_load(settings)
            assert(loaded.mahjong_camera_right[1].key == "OBS_KEY_F8")
            assert(#loaded.mahjong_camera_left == 0)
            assert(#loaded.mahjong_camera_up == 0)
            local p = script_properties()
            p.restore_direction_keys.callback(p)
            assert(loaded.mahjong_camera_left[1].key == "OBS_KEY_LEFT")
            script_save(settings)
            assert(settings.hotkey_left[1].key == "OBS_KEY_LEFT")
        """)

    def test_relative_directions_wrap_and_ignore_key_release(self):
        self.run_lua("""
            script_load(settings)
            handlers.mahjong_camera_right(false)
            assert(mutations == 0)
            handlers.mahjong_camera_right(true); visible("B")
            handlers.mahjong_camera_right(true); visible("C")
            handlers.mahjong_camera_right(true); visible("D")
            handlers.mahjong_camera_right(true); visible("A")
            handlers.mahjong_camera_left(true); visible("D")
            handlers.mahjong_camera_up(true); visible("B")
            assert(scene_releases == 6)
        """)

    def test_restore_survives_immediate_reload_without_obs_save_callback(self):
        self.run_lua("""
            settings.hotkey_right = {}
            settings.hotkey_left = {}
            settings.hotkey_up = {}
            script_load(settings)
            assert(data_refs[settings] == 2)
            local p = script_properties()
            p.restore_direction_keys.callback(p)
            assert(settings.hotkey_right[1].key == "OBS_KEY_RIGHT")
            assert(settings.hotkey_left[1].key == "OBS_KEY_LEFT")
            assert(settings.hotkey_up[1].key == "OBS_KEY_UP")
            -- OBS reload skips script_save: unload, then load the same settings.
            script_unload()
            assert(data_refs[settings] == 1)
            loaded = {}
            script_load(settings)
            assert(loaded.mahjong_camera_right[1].key == "OBS_KEY_RIGHT")
            assert(loaded.mahjong_camera_left[1].key == "OBS_KEY_LEFT")
            assert(loaded.mahjong_camera_up[1].key == "OBS_KEY_UP")
            script_unload()
            assert(data_refs[settings] == 1)
            for data, count in pairs(data_refs) do
                if data ~= settings then assert(count == 0, "temporary data leaked") end
            end
        """)

    def test_test_buttons_and_explicit_initial_camera_recover_safely(self):
        self.run_lua("""
            script_load(settings)
            items.B.visible = true
            handlers.mahjong_camera_right(true)
            assert(mutations == 0)
            local p = script_properties()
            p.start_camera_1.callback(p); visible("A")
            p.test_next.callback(p); visible("B")
            p.test_previous.callback(p); visible("A")
            p.test_opposite.callback(p); visible("C")
            items.C.visible = false
            local before = mutations
            handlers.mahjong_camera_up(true)
            assert(mutations == before)
            p.start_camera_1.callback(p); visible("A")
            assert(p.camera_status.description:find("A", 1, true))
        """)

    def test_bad_missing_or_duplicate_sources_never_mutate(self):
        self.run_lua("""
            script_load(settings)
            local p = script_properties()
            for _, bad in ipairs({"", "missing", "A"}) do
                settings.camera_2 = bad
                script_update(settings)
                handlers.mahjong_camera_right(true)
                p.start_camera_1.callback(p)
                assert(mutations == 0)
            end
            settings.camera_2 = "B"
            script_update(settings)
            has_scene = false
            handlers.mahjong_camera_left(true)
            p.start_camera_1.callback(p)
            assert(mutations == 0)
        """)

    def test_whitespace_is_trimmed_and_diagnostics_release_scene(self):
        self.run_lua("""
            settings.camera_1 = "  A  "
            script_load(settings)
            local p = script_properties()
            assert(p.camera_status.description:find("A", 1, true))
            p.check_cameras.callback(p)
            handlers.mahjong_camera_right(true)
            visible("B")
            assert(scene_releases == 4)
        """)


if __name__ == "__main__":
    unittest.main()
