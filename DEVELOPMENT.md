# 开发与发布

## 分支

- `dev`：日常开发，**只在本地提交，不 push 到线上**。
- `main`：发布分支，只接收从 `dev` 合并的发布提交。

## 日常开发

```powershell
git switch dev
# 修改、测试
git add .
git commit -m "..."
```

## 发布

```powershell
git switch main
git pull --ff-only
git merge --no-ff dev
git push origin main

git tag v0.1.3
git push origin v0.1.3
```

推送 `v*` tag 后，Actions 会构建并发布 `ASPA-win-x64.zip` 和 `.sha256`。

发布后回到开发分支，并把 main 合回来，避免长期分叉：

```powershell
git switch dev
git merge main
```

## 本地构建

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
  -Version 0.1.5 -Repo Qiiamx/ArknightsStrongholdProtocolAssistant `
  -PythonExe .\.build-venv\Scripts\python.exe
```

产物：`dist\ASPA-win-x64.zip`、`dist\ASPA-win-x64.zip.sha256`。
