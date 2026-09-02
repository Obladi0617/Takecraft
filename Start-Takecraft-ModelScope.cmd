@echo off
setlocal
set "ROOT=%~dp0"

if not defined FILMAGENT_LLM_API_KEY (
  echo FILMAGENT_LLM_API_KEY is not set in Windows user environment variables.
  pause
  exit /b 1
)

if not defined FILMAGENT_LLM_BASE_URL set "FILMAGENT_LLM_BASE_URL=https://api-inference.modelscope.cn"
if not defined FILMAGENT_LLM_MODEL set "FILMAGENT_LLM_MODEL=Qwen/Qwen3.5-35B-A3B"
set "FILMAGENT_LLM_BACKEND=openai"
set "FILMAGENT_LLM_MAX_RETRIES=2"
set "FILMAGENT_IMAGE_BACKEND=mock"
set "FILMAGENT_VIDEO_BACKEND=mock"
set "FILMAGENT_VLM_BACKEND=mock"

start "Takecraft ModelScope Backend" /D "%ROOT%backend" cmd /k "set PATH=%ROOT%.tools\ffmpeg-9.0.1-essentials_build\bin;%%PATH%% && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765"
start "Takecraft Frontend" /D "%ROOT%frontend" cmd /k "set PATH=%ROOT%.tools\node-v24.19.0-win-x64;%%PATH%% && set npm_config_cache=%ROOT%.tools\npm-cache && %ROOT%.tools\node-v24.19.0-win-x64\npm.cmd run dev -- --host 127.0.0.1"

timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:5173"
endlocal
