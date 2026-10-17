import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const rootDir = path.resolve(frontendDir, '..', '..')
const backendDir = path.join(rootDir, 'apps', 'backend')
const reportDir = path.join(rootDir, 'docs', 'verification')
const baseUrl = process.env.UI_ACCEPTANCE_BASE_URL || 'http://127.0.0.1:5173'
const backendHealthUrl = process.env.UI_ACCEPTANCE_BACKEND_HEALTH_URL || 'http://127.0.0.1:8000/health'
const PREFLIGHT_DEADLINE_MS = 15_000
const PREFLIGHT_RETRY_MS = 500

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function pickPython() {
  const configured = process.env.UI_ACCEPTANCE_PYTHON
  if (configured) return configured
  const projectVenv = path.join(rootDir, '.venv', 'Scripts', 'python.exe')
  if (fs.existsSync(projectVenv)) return projectVenv
  return 'python'
}

function latestReport(pattern) {
  if (!fs.existsSync(reportDir)) return undefined
  return fs
    .readdirSync(reportDir)
    .filter((name) => pattern.test(name))
    .map((name) => {
      const fullPath = path.join(reportDir, name)
      return { fullPath, mtimeMs: fs.statSync(fullPath).mtimeMs }
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs)[0]?.fullPath
}

function waitForUrl(url, label) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 10_000
    const probe = () => {
      const request = http.get(url, (response) => {
        response.resume()
        if (response.statusCode && response.statusCode >= 200 && response.statusCode < 500) {
          resolve()
          return
        }
        retry()
      })
      request.on('error', retry)
      request.setTimeout(2_000, () => {
        request.destroy()
        retry()
      })
    }
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error(`${label} is not reachable: ${url}`))
        return
      }
      setTimeout(probe, 500)
    }
    probe()
  })
}

function getJson(url, label) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { headers: { Connection: 'close' } }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        body += chunk
      })
      response.on('end', () => {
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`${label} returned HTTP ${response.statusCode}: ${body.slice(0, 500)}`))
          return
        }
        try {
          resolve(JSON.parse(body))
        } catch (error) {
          reject(new Error(`${label} did not return JSON: ${error.message}`))
        }
      })
    })
    request.on('error', (error) => reject(new Error(`${label} request failed: ${error.message}`)))
    request.setTimeout(5_000, () => {
      request.destroy()
      reject(new Error(`${label} request timed out: ${url}`))
    })
  })
}

async function verifySeededSessionVisible(sessionId, marker) {
  const messagesUrl = new URL(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`, backendHealthUrl)
  const deadline = Date.now() + PREFLIGHT_DEADLINE_MS
  let lastError

  while (Date.now() <= deadline) {
    try {
      const messages = await getJson(messagesUrl.toString(), 'Seeded UI acceptance session visibility check')
      const text = JSON.stringify(messages)
      if (Array.isArray(messages) && text.includes(marker)) {
        return
      }
      lastError = new Error('seeded marker was not present in returned session messages')
    } catch (error) {
      lastError = error
    }

    if (Date.now() <= deadline) {
      await sleep(PREFLIGHT_RETRY_MS)
    }
  }

  throw new Error(
    [
      `seeded session is not visible to the running backend: ${sessionId}`,
      `Expected marker: ${marker}`,
      `Backend check: ${messagesUrl}`,
      `DATABASE_URL: ${process.env.DATABASE_URL || '<unset>'}`,
      `Last preflight error: ${lastError?.message || 'unknown error'}`,
      'Transient connection resets such as read ECONNRESET are retried before this failure is reported.',
      'Ensure the running backend and this UI acceptance script use the same database environment.',
    ].join('\n'),
  )
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env,
    stdio: options.stdio ?? 'inherit',
    encoding: options.encoding,
    shell: false,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    if (result.stdout) process.stdout.write(result.stdout)
    if (result.stderr) process.stderr.write(result.stderr)
    process.exit(result.status ?? 1)
  }
  return result
}

await waitForUrl(backendHealthUrl, 'Backend')
await waitForUrl(baseUrl, 'Frontend')

const python = pickPython()

if (process.env.UI_ACCEPTANCE_SKIP_MVP !== 'true') {
  run(python, ['-m', 'app.verification.mvp_acceptance'], {
    cwd: backendDir,
    stdio: 'pipe',
    encoding: 'utf8',
    env: {
      ...process.env,
      OPENROUTER_MOCK: 'true',
      OPENROUTER_MOCK_LATENCY_MS: '0',
      PYTHONIOENCODING: 'utf-8',
    },
  })

  const mvpReportPath = latestReport(/^mvp_acceptance_.*\.json$/)
  if (!mvpReportPath) {
    throw new Error(`No MVP acceptance report was created in ${reportDir}`)
  }
  console.log(`[ui-acceptance] mvp_report=${mvpReportPath}`)
} else {
  console.log('[ui-acceptance] mvp acceptance skipped by UI_ACCEPTANCE_SKIP_MVP=true')
}

run(python, ['-m', 'app.verification.scope_acceptance'], {
  cwd: backendDir,
  stdio: 'pipe',
  encoding: 'utf8',
  env: {
    ...process.env,
    OPENROUTER_MOCK: 'true',
    OPENROUTER_MOCK_LATENCY_MS: '0',
    PYTHONIOENCODING: 'utf-8',
  },
})

const reportPath = latestReport(/^scope_acceptance_.*\.json$/)
if (!reportPath) {
  throw new Error(`No scope acceptance report was created in ${reportDir}`)
}

const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'))
const sessionId = report?.snapshots?.ui_session?.session_id
const marker = report?.fixture?.marker_a
if (!sessionId || !marker) {
  throw new Error(`Scope report does not contain ui_session.session_id and fixture.marker_a: ${reportPath}`)
}

console.log(`[ui-acceptance] report=${reportPath}`)
console.log(`[ui-acceptance] session=${sessionId}`)

await verifySeededSessionVisible(sessionId, marker)

const playwrightCli = path.join(frontendDir, 'node_modules', '@playwright', 'test', 'cli.js')
run(process.execPath, [playwrightCli, 'test', 'tests/decision-chat-flow.ui.spec.ts', '--config=playwright.config.ts'], {
  cwd: frontendDir,
  env: {
    ...process.env,
    UI_ACCEPTANCE_BASE_URL: baseUrl,
    UI_ACCEPTANCE_SESSION_ID: sessionId,
    UI_ACCEPTANCE_MARKER_A: marker,
    UI_ACCEPTANCE_REPORT_PATH: reportPath,
  },
})
