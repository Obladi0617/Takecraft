# Takecraft + DGX Spark 使用指南

本项目的正式测试链路是：ModelScope 生成剧本与分镜图，DGX Spark 上的 ComfyUI + MiniMax H3 将分镜图生成带声音的 AI 视频，本机 FFmpeg 完成代理、选片和成片渲染。

## 已验证配置

- DGX SSH：`Developer@106.13.186.155:6057`
- ComfyUI：DGX 本机 `127.0.0.1:8188`
- 视频模型：`minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- 输出：约 864×480、24 FPS、H.264 + AAC
- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8765`

密码和 API Token 不得写入仓库、README 或截图。ModelScope Token 只保存在 Windows 用户环境变量中。

## 每次使用

### 1. 启动 DGX ComfyUI

打开 PowerShell：

```powershell
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -p 6057 Developer@106.13.186.155
```

登录后在 DGX 执行：

```bash
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate h3-comfy
cd "$HOME/minimax-h3-dgx-spark/ComfyUI"

nohup python main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --reserve-vram 8 \
  > "$HOME/comfyui-takecraft.log" 2>&1 &
```

检查服务：

```bash
curl -s http://127.0.0.1:8188/system_stats | head -c 300
```

看到以 `{` 开头的 JSON 即正常。若提示端口已占用，说明服务已经运行，不要重复启动。

### 2. 建立 SSH 隧道

另开一个 PowerShell 窗口：

```powershell
ssh -N -L 8188:127.0.0.1:8188 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -p 6057 Developer@106.13.186.155
```

输入密码后窗口保持空白属于正常状态。使用期间不要关闭，不要把 8188 端口直接暴露到公网。

本机检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8188/system_stats
```

### 3. 启动 Takecraft

确认 Windows 用户环境变量已有 `FILMAGENT_LLM_API_KEY`，然后双击：

```text
Start-Takecraft-ModelScope.cmd
```

启动器会使用：

```text
FILMAGENT_LLM_BACKEND=openai
FILMAGENT_IMAGE_BACKEND=modelscope
FILMAGENT_VIDEO_BACKEND=comfyui
FILMAGENT_COMFYUI_BASE_URL=http://127.0.0.1:8188
```

浏览器打开 `http://127.0.0.1:5173`。Swagger 接口文档在 `http://127.0.0.1:8765/docs`。

## 网页操作

1. 新建 `AUTO` 项目。
2. 输入一句话创意，启动全自动流程。
3. 等待剧本、资产设计和分镜完成。
4. 视频阶段会把锁定分镜上传至 DGX，并调用 MiniMax H3。
5. 单条视频通常需要数分钟；多个镜头会依次排队。
6. 系统自动下载 Take、审核、选片、剪辑并渲染最终 MP4。

运行期间不要关闭 SSH 隧道、DGX ComfyUI 或 Takecraft 后端。

## API 工作方式

适配器：`backend/app/generators/cloud.py`

工作流：`backend/app/generators/workflows/minimax_h3_i2v.api.json`

调用流程：

1. `POST /upload/image` 上传首帧；
2. `POST /prompt` 提交 MiniMax H3 工作流；
3. `GET /history/{prompt_id}` 轮询；
4. `GET /view` 下载 MP4 到 H 盘；
5. FFmpeg 生成 Proxy，并进入审核、剪辑和渲染。

默认 16:9、0.4MP、24 FPS。MiniMax H3 最短输出约 5 秒，并生成同步音频。

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `FILMAGENT_COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | SSH 隧道入口 |
| `FILMAGENT_COMFYUI_TIMEOUT` | `3600` | 单条视频最长等待秒数 |
| `FILMAGENT_COMFYUI_MEGAPIXELS` | `0.4` | 生成分辨率规模 |
| `FILMAGENT_VIDEO_GENERATION_CONCURRENCY` | `1` | DGX 并发数，建议保持 1 |

## 常见故障

### 8188 拒绝连接

SSH 隧道未建立、已断开，或 DGX ComfyUI 没有运行。依次重做“启动 DGX”和“建立隧道”。

### SSH Permission denied

密码未通过。切换英文输入法重试；密码输入时不会显示字符。

### 任务长时间 RUNNING

首次加载模型会比较慢。检查队列：

```powershell
Invoke-RestMethod http://127.0.0.1:8188/queue
```

`queue_running` 有任务通常表示仍在计算。

### DGX 报错或显存不足

查看 DGX 日志：

```bash
tail -n 100 "$HOME/comfyui-takecraft.log"
```

保持视频并发为 1，不要同时在 ComfyUI 页面重复提交。

### 临时不用 DGX

可将视频后端临时降级为本机分镜运镜：

```powershell
$env:FILMAGENT_VIDEO_BACKEND="image_motion"
```

它能生成正常可播放视频，但不包含人物的生成式动作。

## 停止

关闭 SSH 隧道窗口即可断开访问。停止 Takecraft 可双击 `Stop-Takecraft.cmd`。

如需停止 DGX ComfyUI，在 DGX 执行：

```bash
pkill -f "python main.py --listen 127.0.0.1 --port 8188"
```