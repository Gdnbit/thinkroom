import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 300000,  // 300s — thinking models (claude/gemini) can take 2-3 min
})

export default api

// 验证中心专用：超时 17 分钟（全链路验证含多轮 LLM 调用，后端 run-suite 超时 900s + 120s buffer）
export const apiLong = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 1020000,
})
