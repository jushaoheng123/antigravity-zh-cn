import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def resolve_target_asar() -> Path:
    env_path = os.environ.get("AGY_ASAR_PATH")
    if env_path:
        return Path(env_path)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidate = Path(local_app) / "Programs" / "antigravity" / "resources" / "app.asar"
        if candidate.exists():
            return candidate
        alt = Path(local_app) / "Programs" / "Antigravity" / "resources" / "app.asar"
        if alt.exists():
            return alt
    return Path.home() / "AppData" / "Local" / "Programs" / "Antigravity" / "resources" / "app.asar"


ASAR_TARGET = resolve_target_asar()
ORIGINAL_BACKUP_DIR = BASE_DIR / "backup" / "original"
BY_VERSION_DIR = ORIGINAL_BACKUP_DIR / "by-version"
ORIGINAL_ASAR = ORIGINAL_BACKUP_DIR / "app.asar"
UNPACKED_DIR = BASE_DIR / "unpacked"
BUILD_DIR = BASE_DIR / "build"
BUILD_ASAR = BUILD_DIR / "app.asar"
MANIFEST_PATH = BUILD_DIR / "manifest.json"
PATCH_MARKER_REL = Path("dist") / "__agy_zh_cn_patch__.json"

BLOCK_SIZE = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def read_asar_header(fp):
    hdr = fp.read(16)
    _, u1, _, str_len = struct.unpack("<IIII", hdr)
    meta = json.loads(fp.read(str_len).decode("utf-8"))
    data_start = 8 + u1
    return meta, data_start


def read_asar_entry(asar_path: Path, rel_parts: list[str]) -> bytes | None:
    with open(asar_path, "rb") as f:
        meta, data_start = read_asar_header(f)
        node = meta
        for part in rel_parts:
            files = node.get("files")
            if not files or part not in files:
                return None
            node = files[part]
        if "offset" not in node:
            return None
        f.seek(data_start + int(node["offset"]))
        return f.read(int(node["size"]))


def get_asar_info(asar_path: Path) -> dict:
    pkg_bytes = read_asar_entry(asar_path, ["package.json"])
    pkg = json.loads(pkg_bytes.decode("utf-8")) if pkg_bytes else {}
    marker_bytes = read_asar_entry(asar_path, list(PATCH_MARKER_REL.parts))
    marker = json.loads(marker_bytes.decode("utf-8")) if marker_bytes else None

    has_zh_hook = False
    if marker is None:
        utils_bytes = read_asar_entry(asar_path, ["dist", "utils.js"])
        if utils_bytes and b"injectAgyZhCn" in utils_bytes:
            has_zh_hook = True
    return {
        "sha256": sha256_file(asar_path),
        "version": str(pkg.get("version", "unknown")),
        "is_patched": bool(marker is not None or has_zh_hook),
        "marker": marker,
    }


def resolve_source_asar() -> tuple[Path, str, str]:
    """
    Auto-adapt to Antigravity updates:
    - If live resources/app.asar is unpatched, snapshot it under backup/original/by-version/<ver>-<sha8>/app.asar.
    - If live resources/app.asar is already patched, resolve the matching clean original backup.
    """
    ORIGINAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BY_VERSION_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not ASAR_TARGET.exists():
        raise FileNotFoundError(f"Target app.asar not found: {ASAR_TARGET}")

    live = get_asar_info(ASAR_TARGET)
    print(
        f"[detect] Live app.asar: version={live['version']} sha256={live['sha256'][:12]} patched={live['is_patched']}"
    )

    if not live["is_patched"]:
        ver_dir = BY_VERSION_DIR / f"{live['version']}-{live['sha256'][:8]}"
        ver_dir.mkdir(parents=True, exist_ok=True)
        ver_backup = ver_dir / "app.asar"
        if not ver_backup.exists():
            shutil.copy2(ASAR_TARGET, ver_backup)
            print(f"[backup] Saved clean official app.asar ({live['version']}) -> {ver_backup}")
        else:
            print(f"[backup] Clean official backup already exists -> {ver_backup}")
        shutil.copy2(ver_backup, ORIGINAL_ASAR)
        return ver_backup, live["version"], live["sha256"]

    if live["marker"] and live["marker"].get("source_sha256"):
        src_sha = live["marker"]["source_sha256"]
        src_ver = live["marker"].get("source_version", live["version"])
        candidate = BY_VERSION_DIR / f"{src_ver}-{src_sha[:8]}" / "app.asar"
        if candidate.exists():
            print(f"[source] Matched clean backup by marker -> {candidate}")
            return candidate, src_ver, src_sha

    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if manifest.get("patched_sha256") == live["sha256"]:
            src_path = Path(manifest.get("source_backup", ""))
            if src_path.exists():
                print(f"[source] Matched clean backup by manifest -> {src_path}")
                return src_path, manifest["source_version"], manifest["source_sha256"]

    if ORIGINAL_ASAR.exists():
        orig_info = get_asar_info(ORIGINAL_ASAR)
        if not orig_info["is_patched"]:
            ver_dir = BY_VERSION_DIR / f"{orig_info['version']}-{orig_info['sha256'][:8]}"
            ver_dir.mkdir(parents=True, exist_ok=True)
            ver_backup = ver_dir / "app.asar"
            if not ver_backup.exists():
                shutil.copy2(ORIGINAL_ASAR, ver_backup)
            print(f"[source] Using existing clean backup -> {ver_backup}")
            return ver_backup, orig_info["version"], orig_info["sha256"]

    raise RuntimeError(
        "Live app.asar is already patched, and no matching clean original backup was found. "
        "Please restore or reinstall official Antigravity first."
    )


def unpack_asar(asar_path: Path, out_dir: Path) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(asar_path, "rb") as f:
        meta, data_start = read_asar_header(f)

        def _extract(node: dict, rel: Path):
            for name, info in node.get("files", {}).items():
                target = rel / name
                if "files" in info:
                    target.mkdir(parents=True, exist_ok=True)
                    _extract(info, target)
                elif info.get("unpacked"):
                    continue
                else:
                    offset = data_start + int(info["offset"])
                    size = int(info["size"])
                    f.seek(offset)
                    data = f.read(size)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)

        _extract(meta, out_dir)
    print(f"[unpack] Unpacked {asar_path} -> {out_dir}")
    return meta


def apply_sub(text: str, old: str, new: str, label: str, required: bool = False) -> tuple[str, bool]:
    count = text.count(old)
    if count == 1:
        print(f"  [OK] {label}")
        return text.replace(old, new, 1), True
    msg = f"  [{'FAIL' if required else 'WARN'}] {label}: matched {count} times (skipped)"
    print(msg)
    if required:
        raise RuntimeError(msg)
    return text, False


def apply_regex(text: str, pattern: str, repl: str, label: str, required: bool = False) -> tuple[str, bool]:
    new_text, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if count == 1:
        print(f"  [OK] {label}")
        return new_text, True
    msg = f"  [{'FAIL' if required else 'WARN'}] {label}: regex matched {count} times (skipped)"
    print(msg)
    if required:
        raise RuntimeError(msg)
    return text, False


def patch_files(root: Path, source_ver: str, source_sha: str) -> list[Path]:
    modified: list[Path] = []

    # 1. dist/tray.js
    tray_path = root / "dist" / "tray.js"
    if tray_path.exists():
        tray_txt = tray_path.read_text(encoding="utf-8")
        tray_txt, ok = apply_sub(
            tray_txt,
            "            countItem.label =\n"
            "                (count > 0 ? `${count}` : 'No') +\n"
            "                    ' agent' +\n"
            "                    (count === 1 ? '' : 's') +\n"
            "                    ' running';",
            "            countItem.label = count > 0 ? `正在运行 ${count} 个智能体` : '暂无运行中的智能体';",
            "tray.js countItem.label",
        )
        if ok:
            tray_path.write_text(tray_txt, encoding="utf-8", newline="\n")
            modified.append(tray_path)

    # 2. dist/updater.js
    upd_path = root / "dist" / "updater.js"
    if upd_path.exists():
        upd_txt = upd_path.read_text(encoding="utf-8")
        upd_txt, ok = apply_sub(
            upd_txt,
            '    MenuUpdateStep["CheckForUpdates"] = "Check for Updates";\n'
            '    MenuUpdateStep["CheckingForUpdates"] = "Checking for Updates...";\n'
            '    MenuUpdateStep["DownloadingUpdate"] = "Downloading Update...";\n'
            '    MenuUpdateStep["RestartToUpdate"] = "Restart to Update";',
            '    MenuUpdateStep["CheckForUpdates"] = "检查更新";\n'
            '    MenuUpdateStep["CheckingForUpdates"] = "正在检查更新...";\n'
            '    MenuUpdateStep["DownloadingUpdate"] = "正在下载更新...";\n'
            '    MenuUpdateStep["RestartToUpdate"] = "重启以更新";',
            "updater.js MenuUpdateStep",
        )
        if ok:
            upd_path.write_text(upd_txt, encoding="utf-8", newline="\n")
            modified.append(upd_path)

    # 3. dist/main.js
    main_path = root / "dist" / "main.js"
    if main_path.exists():
        main_txt = main_path.read_text(encoding="utf-8")
        main_txt, ok1 = apply_sub(
            main_txt,
            "                    label: 'New Window',\n"
            "                    click: () => (0, utils_1.createWindow)(url, storageManager, { cascade: true }),",
            "                    label: '新建窗口',\n"
            "                    click: () => (0, utils_1.createWindow)(url, storageManager, { cascade: true }),",
            "main.js dock New Window",
        )
        main_txt, ok2 = apply_sub(
            main_txt,
            "            {\n"
            "                id: 'running-agents',\n"
            "                label: 'No agents running',\n"
            "                enabled: false,\n"
            "            },\n"
            "            { type: 'separator' },\n"
            "            {\n"
            "                label: `Open ${electron_1.app.getName()}`,\n"
            "                click: () => (0, utils_1.showOrCreateWindow)((0, languageServer_1.getLsPort)()),\n"
            "            },\n"
            "            {\n"
            "                label: 'Quit',",
            "            {\n"
            "                id: 'running-agents',\n"
            "                label: '暂无运行中的智能体',\n"
            "                enabled: false,\n"
            "            },\n"
            "            { type: 'separator' },\n"
            "            {\n"
            "                label: `打开 ${electron_1.app.getName()}`,\n"
            "                click: () => (0, utils_1.showOrCreateWindow)((0, languageServer_1.getLsPort)()),\n"
            "            },\n"
            "            {\n"
            "                label: '退出',",
            "main.js tray labels",
        )
        main_txt, ok3 = apply_sub(
            main_txt,
            "    const options = {\n"
            "        type: 'question',\n"
            "        buttons: ['Cancel', 'Quit'],\n"
            "        defaultId: 1,\n"
            "        cancelId: 0,\n"
            "        title: 'Confirm Quit',\n"
            "        message: 'Are you sure you want to quit?',\n"
            "        detail: 'There may be agents or background tasks running.',\n"
            "    };",
            "    const options = {\n"
            "        type: 'question',\n"
            "        buttons: ['取消', '退出'],\n"
            "        defaultId: 1,\n"
            "        cancelId: 0,\n"
            "        title: '确认退出',\n"
            "        message: '确定要退出吗？',\n"
            "        detail: '当前可能仍有智能体或后台任务正在运行。',\n"
            "    };",
            "main.js quit confirm",
        )
        if ok1 or ok2 or ok3:
            main_path.write_text(main_txt, encoding="utf-8", newline="\n")
            modified.append(main_path)

    # 4. dist/menu.js
    menu_path = root / "dist" / "menu.js"
    if menu_path.exists():
        menu_txt = menu_path.read_text(encoding="utf-8")
        menu_helper = """
const AGY_MENU_ZH_MAP = {
    'File': '文件',
    'Edit': '编辑',
    'View': '视图',
    'Window': '窗口',
    'Help': '帮助',
    'New Window': '新建窗口',
    'Connect to WSL': '连接到 WSL',
    'Reopen Locally': '在本地重新打开',
    'Docs': '文档',
    'Undo': '撤销',
    'Redo': '重做',
    'Cut': '剪切',
    'Copy': '复制',
    'Paste': '粘贴',
    'Paste and Match Style': '粘贴并匹配样式',
    'Delete': '删除',
    'Select All': '全选',
    'Reload': '重新加载',
    'Force Reload': '强制重新加载',
    'Toggle Developer Tools': '切换开发者工具',
    'Actual Size': '实际大小',
    'Zoom In': '放大',
    'Zoom Out': '缩小',
    'Toggle Full Screen': '切换全屏',
    'Minimize': '最小化',
    'Zoom': '缩放',
    'Close': '关闭',
    'Close Window': '关闭窗口',
    'Quit': '退出',
    'Exit': '退出',
    'Learn More': '了解更多',
    'Check for Updates': '检查更新'
};
function translateMenuLabel(label) {
    if (!label) return label;
    const clean = label.replace(/&/g, '');
    return AGY_MENU_ZH_MAP[label] || AGY_MENU_ZH_MAP[clean] || label;
}
function cloneTranslatedMenu(srcMenu) {
    if (!srcMenu || !Array.isArray(srcMenu.items)) return srcMenu;
    const nextMenu = new electron_1.Menu();
    for (const item of srcMenu.items) {
        const opts = {
            type: item.type,
            enabled: item.enabled,
            visible: item.visible,
        };
        if (item.id) opts.id = item.id;
        if (item.label) opts.label = translateMenuLabel(item.label);
        if (item.sublabel) opts.sublabel = item.sublabel;
        if (item.toolTip) opts.toolTip = item.toolTip;
        if (item.accelerator) opts.accelerator = item.accelerator;
        if (item.acceleratorWorksWhenHidden !== undefined) {
            opts.acceleratorWorksWhenHidden = item.acceleratorWorksWhenHidden;
        }
        if (item.registerAccelerator !== undefined) {
            opts.registerAccelerator = item.registerAccelerator;
        }
        if (item.type === 'checkbox' || item.type === 'radio') {
            opts.checked = item.checked;
        }
        if (item.submenu) {
            opts.submenu = cloneTranslatedMenu(item.submenu);
        }
        else if (item.role) {
            opts.role = item.role;
        }
        else if (typeof item.click === 'function') {
            opts.click = item.click;
        }
        nextMenu.append(new electron_1.MenuItem(opts));
    }
    return nextMenu;
}
"""
        menu_txt, ok1 = apply_sub(
            menu_txt,
            "function configureViewZoomMenu(appMenu) {",
            menu_helper + "\nfunction configureViewZoomMenu(appMenu) {",
            "menu.js helper inject",
        )
        ok2 = ok3 = ok4 = ok5 = False
        if ok1:
            menu_txt, ok2 = apply_sub(
                menu_txt,
                "    configureViewZoomMenu(menu);\n"
                "    (0, debugConsole_1.configureDebugConsoleMenu)(menu);\n"
                "    // Re-apply the menu so the change takes effect.\n"
                "    electron_1.Menu.setApplicationMenu(menu);\n"
                "    // Asynchronously adds \"Connect to WSL\" (Windows only, when WSL is present).\n"
                "    void addWslConnectMenu(menu);",
                "    configureViewZoomMenu(menu);\n"
                "    (0, debugConsole_1.configureDebugConsoleMenu)(menu);\n"
                "    // Re-apply the menu with Chinese labels.\n"
                "    const localizedMenu = cloneTranslatedMenu(menu);\n"
                "    electron_1.Menu.setApplicationMenu(localizedMenu);\n"
                "    // Asynchronously adds \"Connect to WSL\" (Windows only, when WSL is present).\n"
                "    void addWslConnectMenu(localizedMenu);",
                "menu.js setupApplicationMenu localize",
            )
            menu_txt, ok3 = apply_sub(
                menu_txt,
                "    return { label: 'Connect to WSL', submenu };",
                "    return { label: '连接到 WSL', submenu };",
                "menu.js Connect to WSL label",
            )
            menu_txt, ok4 = apply_sub(
                menu_txt,
                "    return { label: 'Reopen Locally', click: () => relaunchWithWslDistro('') };",
                "    return { label: '在本地重新打开', click: () => relaunchWithWslDistro('') };",
                "menu.js Reopen Locally label",
            )
            menu_txt, ok5 = apply_sub(
                menu_txt,
                "    addItemToSubmenu(menu, 'File', 1, new electron_1.MenuItem(template));\n"
                "    // Local mode is not a WSL distro, so \"Reopen Locally\" is a sibling item\n"
                "    // rather than an entry in the distro submenu.\n"
                "    const reopen = wslReopenLocallyTemplate();\n"
                "    if (reopen) {\n"
                "        addItemToSubmenu(menu, 'File', 2, new electron_1.MenuItem(reopen));\n"
                "    }\n"
                "    electron_1.Menu.setApplicationMenu(menu);",
                "    const fileTarget = menu.items.find((i) => i.label === '文件' || i.label === 'File')?.label || '文件';\n"
                "    addItemToSubmenu(menu, fileTarget, 1, new electron_1.MenuItem(template));\n"
                "    const reopen = wslReopenLocallyTemplate();\n"
                "    if (reopen) {\n"
                "        addItemToSubmenu(menu, fileTarget, 2, new electron_1.MenuItem(reopen));\n"
                "    }\n"
                "    electron_1.Menu.setApplicationMenu(menu);",
                "menu.js addWslConnectMenu fileTarget",
            )
        if any([ok1, ok2, ok3, ok4, ok5]):
            menu_path.write_text(menu_txt, encoding="utf-8", newline="\n")
            modified.append(menu_path)

    # 5. dist/utils.js (DOM localization injector)
    utils_path = root / "dist" / "utils.js"
    utils_txt = utils_path.read_text(encoding="utf-8")
    zh_base_js = json.dumps(str(BASE_DIR))

    zh_injector_body = f"""
    const injectAgyZhCn = (eventName) => {{
        if (win.isDestroyed?.() || hubContents.isDestroyed?.()) {{
            return;
        }}
        try {{
            const fsNode = require('fs');
            const pathNode = require('path');
            const zhBaseDir = {zh_base_js};
            const dictFile = pathNode.join(zhBaseDir, 'dict.json');
            const injFile = pathNode.join(zhBaseDir, 'injector.js');
            const logFile = pathNode.join(zhBaseDir, 'inject.log');
            if (!fsNode.existsSync(dictFile) || !fsNode.existsSync(injFile)) {{
                return;
            }}
            const dictJson = fsNode.readFileSync(dictFile, 'utf8');
            const injCode = fsNode.readFileSync(injFile, 'utf8');
            const payload = [
                `window.__AGY_ZH_DICT__ = ${{dictJson}};`,
                injCode,
            ].join('\\n');
            void hubContents.executeJavaScript(payload, true)
                .then((res) => {{
                    try {{
                        fsNode.appendFileSync(logFile, `[${{new Date().toISOString()}}] ${{eventName}} ok ${{JSON.stringify(res)}}\\n`);
                    }} catch {{}}
                }})
                .catch((err) => {{
                    try {{
                        fsNode.appendFileSync(logFile, `[${{new Date().toISOString()}}] ${{eventName}} err ${{String(err)}}\\n`);
                    }} catch {{}}
                }});
        }}
        catch (err) {{
            try {{
                const pathNode = require('path');
                require('fs').appendFileSync(pathNode.join({zh_base_js}, 'inject.log'), `[${{new Date().toISOString()}}] ${{eventName}} catch ${{String(err)}}\\n`);
            }} catch {{}}
        }}
    }};
    const onDomReadyZh = () => injectAgyZhCn('dom-ready');
    const onFinishLoadZh = () => injectAgyZhCn('did-finish-load');
    hubContents.on('dom-ready', onDomReadyZh);
    hubContents.on('did-finish-load', onFinishLoadZh);
    win.on('closed', () => {{
        hubContents.off?.('dom-ready', onDomReadyZh);
        hubContents.off?.('did-finish-load', onFinishLoadZh);
    }});"""

    utils_txt, ok_hook = apply_sub(
        utils_txt,
        "    hubContents.on('will-redirect', guardExternalNavigation);",
        "    hubContents.on('will-redirect', guardExternalNavigation);" + zh_injector_body,
        "utils.js injectAgyZhCn (primary anchor)",
    )
    if not ok_hook:
        utils_txt, _ = apply_regex(
            utils_txt,
            r"([ \t]*\(0,\s*loadingOverlay_1\.attachLoadingOverlay\)\([^;]+\);)",
            zh_injector_body.lstrip("\n") + "\n\\1",
            "utils.js injectAgyZhCn (fallback anchor)",
            required=True,
        )
    utils_path.write_text(utils_txt, encoding="utf-8", newline="\n")
    modified.append(utils_path)

    # 6. Write patch marker inside ASAR
    marker_path = root / PATCH_MARKER_REL
    marker_data = {
        "patch": "antigravity-zh-cn",
        "source_version": source_ver,
        "source_sha256": source_sha,
        "patched_at": datetime.now().isoformat(),
    }
    marker_path.write_text(json.dumps(marker_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return modified


def check_js_syntax(files: list[Path]) -> None:
    node_bin = shutil.which("node")
    if not node_bin:
        print("[check] node not found in PATH, skipping JS syntax check")
    else:
        for f in files:
            res = subprocess.run([node_bin, "--check", str(f)], capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Syntax check failed for {f}:\n{res.stderr}")
        inj = BASE_DIR / "injector.js"
        res = subprocess.run([node_bin, "--check", str(inj)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Syntax check failed for {inj}:\n{res.stderr}")
        print(f"[check] node --check passed for {len(files)} patched JS files + injector.js")
    json.loads((BASE_DIR / "dict.json").read_text(encoding="utf-8"))
    print("[check] dict.json is valid JSON")


def compute_integrity(data: bytes) -> dict:
    full_hash = hashlib.sha256(data).hexdigest()
    blocks = []
    if len(data) == 0:
        blocks.append(hashlib.sha256(b"").hexdigest())
    else:
        for i in range(0, len(data), BLOCK_SIZE):
            blocks.append(hashlib.sha256(data[i : i + BLOCK_SIZE]).hexdigest())
    return {
        "algorithm": "SHA256",
        "hash": full_hash,
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


def repack_asar(orig_meta: dict, src_root: Path, out_asar: Path) -> None:
    out_asar.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[bytes] = []
    cur_offset = 0

    def pack_entry(file_path: Path, executable: bool = False) -> dict:
        nonlocal cur_offset
        data = file_path.read_bytes()
        entry = {
            "size": len(data),
            "offset": str(cur_offset),
            "integrity": compute_integrity(data),
        }
        if executable:
            entry["executable"] = True
        chunks.append(data)
        cur_offset += len(data)
        return entry

    def rebuild_node(orig_node: dict, rel: Path) -> dict:
        out_files = {}
        for name, info in orig_node.get("files", {}).items():
            sub_rel = rel / name
            if "files" in info:
                out_files[name] = rebuild_node(info, sub_rel)
            elif info.get("unpacked"):
                out_files[name] = info
            else:
                out_files[name] = pack_entry(src_root / sub_rel, bool(info.get("executable")))

        if rel == Path("dist"):
            marker_name = PATCH_MARKER_REL.name
            if marker_name not in out_files and (src_root / PATCH_MARKER_REL).exists():
                out_files[marker_name] = pack_entry(src_root / PATCH_MARKER_REL)

        res = {"files": out_files}
        if orig_node.get("unpacked"):
            res["unpacked"] = True
        return res

    new_meta = rebuild_node(orig_meta, Path())
    json_bytes = json.dumps(new_meta, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    str_len = len(json_bytes)
    pad = (4 - (str_len % 4)) % 4
    padded_json = json_bytes + (b"\x00" * pad)
    u2 = 4 + len(padded_json)
    u1 = 4 + u2
    header = struct.pack("<IIII", 4, u1, u2, str_len) + padded_json

    with open(out_asar, "wb") as f:
        f.write(header)
        for c in chunks:
            f.write(c)
    print(f"[pack] Built {out_asar} ({out_asar.stat().st_size} bytes, payload={cur_offset} bytes)")


def verify_asar(asar_path: Path, src_root: Path) -> None:
    with open(asar_path, "rb") as f:
        meta, data_start = read_asar_header(f)
        checked = 0

        def _verify(node: dict, rel: Path):
            nonlocal checked
            for name, info in node.get("files", {}).items():
                sub = rel / name
                if "files" in info:
                    _verify(info, sub)
                elif info.get("unpacked"):
                    continue
                else:
                    off = data_start + int(info["offset"])
                    sz = int(info["size"])
                    f.seek(off)
                    blob = f.read(sz)
                    assert len(blob) == sz, f"Size mismatch on {sub}"
                    expected = (src_root / sub).read_bytes()
                    assert blob == expected, f"Content mismatch on {sub}"
                    assert hashlib.sha256(blob).hexdigest() == info["integrity"]["hash"], f"Hash mismatch on {sub}"
                    checked += 1

        _verify(meta, Path())
    print(f"[verify] Verified {checked} packed entries in {asar_path} (size + bytes + sha256 all OK)")


def main() -> None:
    src_asar, src_ver, src_sha = resolve_source_asar()
    orig_meta = unpack_asar(src_asar, UNPACKED_DIR)
    print("[patch] Applying Antigravity Chinese localization patches:")
    modified = patch_files(UNPACKED_DIR, src_ver, src_sha)
    check_js_syntax(modified)
    repack_asar(orig_meta, UNPACKED_DIR, BUILD_ASAR)
    verify_asar(BUILD_ASAR, UNPACKED_DIR)

    manifest = {
        "source_version": src_ver,
        "source_sha256": src_sha,
        "source_backup": str(src_asar),
        "patched_sha256": sha256_file(BUILD_ASAR),
        "built_at": datetime.now().isoformat(),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[manifest] Updated {MANIFEST_PATH} (source_version={src_ver})")


if __name__ == "__main__":
    main()
