import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
const python = process.env.HROS_PYTHON || (existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
const port = process.env.PORT || '8000';
if (!/^\d{1,5}$/.test(port) || +port < 1 || +port > 65535) throw new Error('PORT must be a valid TCP port');
const child = spawn(python, ['manage.py', 'runserver', `127.0.0.1:${port}`, '--noreload'], {stdio:'inherit', env:{...process.env, DJANGO_SETTINGS_MODULE:process.env.DJANGO_SETTINGS_MODULE || 'payroll_project.settings.development'}});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', error => { console.error(error.message); process.exit(1); });
child.on('exit', code => process.exit(code ?? 1));
