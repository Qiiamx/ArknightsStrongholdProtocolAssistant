# SPA QuickLook

《明日方舟》卫戍协议·规程的**只读**准备界面速查工具：贴在游戏右侧，点一下
「截图识别」，读取本局核心/附加盟约的禁用/启用状态、敌方领袖和特训敌人，
再按你的预设 JSON 给出阵容与大小盟约推荐。

- **只读**：不发送鼠标/键盘、不注入、不读内存；只用 Windows.Graphics.Capture 截游戏客户区。
- **开箱即用**：发布包自带 Python 与 .NET 运行时，不需要装 Python/.NET。
- **自动更新**：启动时检查 GitHub Releases，下载后退出替换并重启。
- **可编辑预设**：侧栏内置阵容/赋分编辑器，也可以直接改 JSON。

> 非官方、非商业玩家工具，与鹰角网络无关。当前识别的是**盟约词条**，不是
> 「本局禁用干员」头像列表；联机准备页尚未单独校准。

## 下载与运行

1. 打开 [Releases](../../releases/latest)，下载 `SPA-QuickLook-win-x64.zip`；
2. 解压到一个**可写**目录（不要只解压 exe）；
3. 双击 `SPA-QuickLook.exe`。

首次运行会在 `%APPDATA%\SPAQuickLook\presets.json` 创建预设副本；
截图保存在 `%APPDATA%\SPAQuickLook\shots\`。

## 使用

1. 启动游戏，停在「确认本局信息」页；
2. 侧栏会自动贴到游戏右侧（右侧放不下时放左侧）；
3. 点 **截图识别**；
4. 看前 3 套推荐；点 **编辑阵容** 调阵容和赋分。

侧栏按钮：

| 按钮 | 作用 |
|---|---|
| 截图识别 | 抓当前游戏客户区并识别、推荐 |
| 编辑阵容 | 打开阵容/赋分编辑器 |
| 贴边 | 重新跟随游戏；拖动标题栏可自由摆放 |
| 重载预设 | 下次识别使用磁盘上的最新预设 |
| 更新 | 检查/安装 GitHub Releases 更新 |

游戏最小化时无法截图；侧栏会提示。工具不会自动恢复窗口或发送输入。

## 预设 JSON

默认文件：`%APPDATA%\SPAQuickLook\presets.json`。结构示例见
[`spa_quicklook/data/presets.example.json`](spa_quicklook/data/presets.example.json)。

```jsonc
{
  "scope": { "season": "规程", "sequence": "序列Ⅰ", "availabilityRule": "anyCoreBanned" },
  "weights": { "priority": 1.0, "enemy": 2.0, "activation": 1.5, "banResilience": 0.5 },
  "lineups": [{
    "id": "latemania-6-arcane",
    "name": "6莱2奥术",
    "primaryCore": "莱塔尼亚",
    "core": { "莱塔尼亚": 6 },
    "minor": { "奥术": 2, "精准": 2 },
    "operators": { "required": ["车尔尼", "黑键"], "core": ["艾雅法拉"], "optional": ["卡涅利安"] },
    "enemyFit": { "频次": 1.0, "隐匿": 1.0, "折射": -1.2 },
    "priority": 10
  }]
}
```

- `availabilityRule`：`anyCoreBanned` / `allCoresBanned` / `coreAndAdditionalBanned`。
- 识别缺格会显示为「未识别」，不会当成可用或禁用。

## 自动更新

发布包内 `update.json` 指定仓库和资产名：

```json
{ "repo": "Qiiamx/SPA-QuickLook", "asset": "SPA-QuickLook-win-x64.zip", "proxy": "" }
```

启动 3 秒后后台检查一次，也可以点「更新」。更新包必须包含 `.sha256`
资产；应用退出后由脚本替换安装目录并重启。国内网络可在 `proxy` 填
`http://127.0.0.1:10808` 之类的本地代理。

## 构建

需要 Windows x64、.NET 8 SDK、Python 3.12。

```powershell
# 1) 构建/依赖（一次）
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt

# 2) 打包
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
  -Version 0.1.0 -Repo Qiiamx/SPA-QuickLook `
  -PythonExe .\.build-venv\Scripts\python.exe
```

产物：`dist\SPA-QuickLook-win-x64.zip` 和 `.sha256`。

PyInstaller 打进 Python/OCR 运行时，`wincap.exe` 用 .NET 自包含单文件；
发布包约 **120 MB（zip）/ 248 MB（解压）**。源码仓库不包含 `research/`、截图、
录像和构建产物。

## 发布到 GitHub

当前目标仓库 `Qiiamx/SPA-QuickLook` 还没有创建；创建空仓库后：

```powershell
cd SPA-QuickLook   # 或你的本地仓库目录
git remote add origin https://github.com/Qiiamx/SPA-QuickLook.git
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

推送 `v*` tag 后，`.github/workflows/release.yml` 会自动构建并创建 Release，
上传 `SPA-QuickLook-win-x64.zip` 与 `.sha256`。如果不想用 Actions，也可以直接把
`dist/SPA-QuickLook-win-x64.zip` 手动上传到 Release，并发布同名 `.sha256`。

`update.json` 中的 `repo` 必须与实际仓库一致；CI 构建会自动使用
`${{ github.repository }}`，本地构建用 `-Repo` 参数指定。

## 目录

```
spa_quicklook/      识别、推荐、侧栏、编辑器、更新
capture/            WGC 截图 helper（C#，发布时自包含）
packaging/          PyInstaller spec + 打包脚本
app.py              窗口入口
run.cmd / run.ps1   源码运行启动器
```

## 隐私与边界

只读截屏；不上传任何游戏画面或账号信息。更新检查只访问 GitHub API。
没有游戏输入、没有注入、没有内存读取。

## License

MIT（见 [LICENSE](LICENSE)）；第三方依赖见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
