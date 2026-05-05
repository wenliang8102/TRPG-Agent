import { execFileSync } from 'node:child_process'

const ports = new Set(['5173', '8000'])
const output = execFileSync('netstat', ['-ano'], { encoding: 'utf8' })
const pids = new Set()

for (const line of output.split(/\r?\n/)) {
  const columns = line.trim().split(/\s+/)
  if (columns.length < 5 || columns[0] !== 'TCP' || columns[3] !== 'LISTENING') continue

  const localPort = columns[1].split(':').at(-1)
  if (ports.has(localPort)) pids.add(columns[4])
}

if (pids.size === 0) {
  console.log('No TRPG dev ports are listening.')
  process.exit(0)
}

for (const pid of pids) {
  console.log(`Stopping dev process pid=${pid}`)
  execFileSync('taskkill', ['/PID', pid, '/F'], { stdio: 'inherit' })
}
