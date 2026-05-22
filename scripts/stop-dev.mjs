import { execFileSync } from 'node:child_process'

const devPorts = new Set(['5173', '8000'])

function addListener(listeners, pid, localPort) {
  if (!listeners.has(pid)) listeners.set(pid, new Set())
  listeners.get(pid).add(localPort)
}

function listDevListenersViaNetstat() {
  const output = execFileSync('netstat', ['-ano'], { encoding: 'utf8' })
  const listeners = new Map()

  for (const line of output.split(/\r?\n/)) {
    const columns = line.trim().split(/\s+/)
    if (columns.length < 5 || columns[0] !== 'TCP' || columns[3] !== 'LISTENING') continue

    const localPort = columns[1].match(/:(\d+)$/)?.[1]
    const pid = columns[4]
    if (!localPort || !devPorts.has(localPort)) continue

    addListener(listeners, pid, localPort)
  }

  return listeners
}

function listDevListenersViaPowerShell() {
  const output = execFileSync(
    'powershell',
    [
      '-NoProfile',
      '-Command',
      'Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress',
    ],
    { encoding: 'utf8' },
  ).trim()

  const rows = output ? JSON.parse(output) : []
  const entries = Array.isArray(rows) ? rows : [rows]
  const listeners = new Map()

  for (const row of entries) {
    const localPort = String(row?.LocalPort ?? '')
    const pid = String(row?.OwningProcess ?? '')
    if (!localPort || !pid || !devPorts.has(localPort)) continue

    addListener(listeners, pid, localPort)
  }

  return listeners
}

function listDevListeners() {
  try {
    return listDevListenersViaNetstat()
  } catch (error) {
    if (error?.code !== 'EPERM' && error?.code !== 'ENOENT') throw error
    try {
      return listDevListenersViaPowerShell()
    } catch {
      console.warn('Unable to inspect dev ports; skipping pre-stop.')
      return new Map()
    }
  }
}

function describeProcess(pid) {
  try {
    const output = execFileSync('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], {
      encoding: 'utf8',
    }).trim()
    const name = output.match(/^"([^"]+)"/)?.[1]
    return name ? `${name} pid=${pid}` : `pid=${pid}`
  } catch {
    return `pid=${pid}`
  }
}

function stopProcessTree(pid, ports) {
  console.log(`Stopping ${describeProcess(pid)} on port(s): ${[...ports].join(', ')}`)

  try {
    execFileSync('taskkill', ['/PID', pid, '/T', '/F'], { stdio: 'inherit' })
  } catch {
    console.warn(`Failed to stop pid=${pid}; verifying ports before failing.`)
  }
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)
}

const listeners = listDevListeners()

if (listeners.size === 0) {
  console.log('No TRPG dev ports are listening.')
  process.exit(0)
}

for (const [pid, ports] of listeners) {
  stopProcessTree(pid, ports)
}

sleep(500)

const remaining = listDevListeners()
if (remaining.size === 0) {
  console.log('TRPG dev ports are free.')
  process.exit(0)
}

console.error('TRPG dev ports are still occupied:')
for (const [pid, ports] of remaining) {
  console.error(`- ${describeProcess(pid)} on port(s): ${[...ports].join(', ')}`)
}
process.exit(1)
