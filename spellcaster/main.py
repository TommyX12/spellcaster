import glob
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time

from argparse import ArgumentParser
from enum import Enum
from spellcaster.util import RepeatedTimer, get_traceback

SPELL_CONFIG_SUFFIX = '.spell.json'
SPELL_STATE_SUFFIX = '.spell_state.json'


def get_default_spell_state_path(config_path):
    if config_path.endswith(SPELL_CONFIG_SUFFIX):
        return config_path[:-len(SPELL_CONFIG_SUFFIX)] + SPELL_STATE_SUFFIX

    return None


class SpellState(object):

    def __init__(self, state_path, config=None):
        if config is None:
            config = {}

        self.path = state_path
        self.last_success = config.get('last_success', 0)

    def to_json(self):
        return {
            'last_success': self.last_success,
        }

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.to_json(), f, indent=2)


class AutoCommandConfig(object):

    UNIT_TO_SECONDS = {
        'hour': 3600,
        'minute': 60,
        'second': 1,
        'day': 86400,
        'week': 604800,
    }

    def __init__(self, config):
        self.command = config.get('command', None)
        if self.command is None:
            raise ValueError('No command specified for auto command')

        self.interval = config.get('interval', 1)
        self.unit = config.get('unit', 'hour')
        if self.unit not in AutoCommandConfig.UNIT_TO_SECONDS:
            raise ValueError('Invalid unit "{}" specified'.format(self.unit))

        unit_to_seconds = AutoCommandConfig.UNIT_TO_SECONDS[self.unit]
        self.interval_seconds = self.interval * unit_to_seconds


class SpellConfig(object):

    def __init__(self, config_path, config):
        self.config_path = config_path
        self.cwd = os.path.dirname(config_path)
        self.name = config.get('name', None)
        if self.name is None:
            raise ValueError(
                'No name specified for spell {}'.format(config_path))

        self.command = config.get('command', None)
        if self.command is None:
            raise ValueError('Spell "{}" has no command'.format(self.name))

        self.state_path = config.get(
            'state_path', os.path.abspath(
                get_default_spell_state_path(config_path)))
        if self.state_path is None:
            raise ValueError(
                'Spell "{}" has no state path given and cannot be deduced from spell path'.format(self.name))

        self.state_path = os.path.join(
            self.cwd, os.path.expanduser(self.state_path))

        self.auto_command = AutoCommandConfig(
            config.get('auto_command', {}))

        self.spell_state = None
        self.read_state()

    def read_state(self):
        if os.path.exists(self.state_path):
            if not os.path.isfile(self.state_path):
                raise ValueError('{} is not a file'.format(self.state_path))

            with open(self.state_path, 'r') as state_file:
                self.spell_state = SpellState(
                    self.state_path, json.load(state_file))

        else:
            self.spell_state = SpellState(self.state_path)


class CasterConfig(object):

    def __init__(self, config_path, config):
        self.spell_configs = {}
        paths = config.get('spells', [])
        cwd = os.path.dirname(config_path)
        for pattern in paths:
            spell_paths = glob.glob(
                os.path.join(cwd, os.path.expanduser(pattern)), recursive=True)
            for spell_path in spell_paths:
                self.read_file(os.path.realpath(spell_path))

    def read_file(self, path):
        with open(path, 'r') as f:
            self.spell_configs[path] = SpellConfig(path, json.load(f))


class SpellStatus(Enum):
    STANDBY = 'standby'
    RUNNING = 'running'
    WARNING = 'warning'
    SUCCESS = 'success'
    ERROR = 'error'


class Spell(object):

    def __init__(self, config, caster):
        self.config = config
        self.caster = caster
        self.thread = None
        self.process = None
        self.status = None
        self.change_status(SpellStatus.STANDBY)

    def is_standby(self):
        with self.caster.lock_status():
            return self.status == SpellStatus.STANDBY

    def is_running(self):
        with self.caster.lock_status():
            return self.status == SpellStatus.RUNNING

    def is_finished(self):
        with self.caster.lock_status():
            return self.status == SpellStatus.SUCCESS or \
                self.status == SpellStatus.WARNING

    def change_status(self, status):
        with self.caster.lock_status():
            if self.status == status:
                return

            self.status = status

        self.caster.spell_status_changed(self)

    def update(self, force_run=False):
        if not self.is_running() and (
                force_run or
                (time.time() - self.config.spell_state.last_success) >=
                self.config.auto_command.interval_seconds):
            self.thread = threading.Thread(target=self.sentinel)
            self.thread.start()

    def run_in_external_terminal(self):
        os_type = platform.system()

        if os_type == 'Darwin':
            with self.caster.lock_tmp_write():
                FILE_TEMPLATE = '#!/bin/bash\ncd "{}"\n{}\nread -p "Press ENTER to continue"\n'
                TEMP_FILE = tempfile.NamedTemporaryFile().name
                with open(TEMP_FILE, 'w') as f:
                    f.write(FILE_TEMPLATE.format(
                        self.config.cwd, self.config.command))
                    os.system('chmod +x "{}"'.format(TEMP_FILE))
                    os.system('open -a Terminal.app "{}"'.format(TEMP_FILE))

        else:
            # TODO: implement more OS
            raise RuntimeError(
                'The operating system {} is not supported yet'.format(os_type))

    def kill(self):
        if self.process is not None:
            self.process.kill()

        else:
            raise RuntimeError('Process is not running')

    def sentinel(self):
        if self.is_running():
            return

        self.change_status(SpellStatus.RUNNING)

        try:
            self.process = subprocess.Popen(
                self.config.auto_command.command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=self.config.cwd)

            stdout, stderr = self.process.communicate()
            self.process.wait()

            process = self.process
            self.process = None

            # # TODO
            # self.caster.print(stdout.decode('utf-8'))
            # self.caster.print(stderr.decode('utf-8'))

            if process.returncode == 0:
                if stderr is not None and stderr.decode('utf-8').strip() != '':
                    self.change_status(SpellStatus.WARNING)
                    self.config.spell_state.last_success = 0
                    self.config.spell_state.save()
                else:
                    self.change_status(SpellStatus.SUCCESS)
                    self.config.spell_state.last_success = time.time()
                    self.config.spell_state.save()

            else:
                self.change_status(SpellStatus.ERROR)
                self.config.spell_state.last_success = 0
                self.config.spell_state.save()

        except Exception as error:
            self.change_status(SpellStatus.ERROR)
            raise error

    def set_config(self, config):
        self.config = config


class Caster(object):

    class AcquireLock(object):
        def __init__(self, caster, lock):
            self.caster = caster
            self.lock = lock

        def __enter__(self):
            self.lock.acquire()

        def __exit__(self, type, value, traceback):
            self.lock.release()

    def __init__(self,
                 config_path,
                 update_interval):
        self.print_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.tmp_write_lock = threading.Lock()
        self.config_path = os.path.abspath(config_path)
        self.caster_dir = os.path.dirname(config_path)
        self.caster_config = None
        self.spells = {}
        self.timer = RepeatedTimer(
            update_interval * 60,
            self.update
        )

    def get_caster_dir(self):
        return self.caster_dir

    def read_config(self):
        with open(self.config_path, 'r') as config_file:
            self.caster_config = CasterConfig(
                self.config_path, json.load(config_file))

    def rerun_spell(self, spell_id):
        if spell_id in self.spells:
            if self.spells[spell_id].is_running():
                raise RuntimeError('Spell "{}" is still running'.format(
                    self.spells[spell_id].config.name))

            self.caster_config.read_file(spell_id)
            self.spells[spell_id].set_config(
                self.caster_config.spell_configs[spell_id])

            self.spells[spell_id].update(force_run=True)

        else:
            raise ValueError('Spell "{}" not found'.format(spell_id))

    def manual_cast_spell(self, spell_id):
        if spell_id in self.spells:
            if not self.spells[spell_id].is_running():
                self.caster_config.read_file(spell_id)
                self.spells[spell_id].set_config(
                    self.caster_config.spell_configs[spell_id])

            self.spells[spell_id].run_in_external_terminal()

        else:
            raise ValueError('Spell "{}" not found'.format(spell_id))

    def spell_status_changed(self, spell):
        self.print('@update: {}'.format(
            json.dumps({
                'spell_path': spell.config.config_path,
                'spell_name': spell.config.name,
                'status': spell.status.value
            })))
        sys.stdout.flush()

    def lock_tmp_write(self):
        return Caster.AcquireLock(self, self.tmp_write_lock)

    def lock_status(self):
        return Caster.AcquireLock(self, self.status_lock)

    def update(self, force_run=False):
        try:
            self.read_config()
            for id in self.caster_config.spell_configs:
                try:
                    if id in self.spells:
                        continue

                    self.spells[id] = Spell(
                        self.caster_config.spell_configs[id], self)

                except Exception:
                    self.print_error()

            for id in list(self.spells.keys()):
                spell = self.spells[id]
                try:
                    if spell.is_running():
                        continue

                    if id not in self.caster_config.spell_configs:
                        del self.spells[id]

                    else:
                        spell.set_config(self.caster_config.spell_configs[id])
                        spell.update(force_run)

                except Exception:
                    self.print_error()

        except Exception:
            self.print_error()

    def handle_request(self, request):
        try:
            request = json.loads(request)
            action = request['action']
            if action == 'cast':
                id = request['spell_id']
                self.print('Casting spell "{}"'.format(
                    self.spells[id].config.name))
                self.manual_cast_spell(id)

            elif action == 'auto_cast':
                id = request['spell_id']
                self.print('Auto-casting spell "{}"'.format(
                    self.spells[id].config.name))
                self.rerun_spell(id)

            elif action == 'kill':
                id = request['spell_id']
                self.print('Killing spell "{}"'.format(
                    self.spells[id].config.name))
                self.spells[id].kill()

            elif action == 'update':
                force_run = request.get('force_run', False) is True
                if force_run:
                    self.print('Running update (force_run=True)')
                    self.print('Running update')

                self.update(force_run)

            else:
                raise ValueError('Unknown action')

        except Exception:
            self.print_error()

    def start(self):
        self.update()
        self.timer.start()
        while True:
            request = str(input())
            self.handle_request(request)

    def print(self, message):
        self.print_lock.acquire()
        print(message)
        self.print_lock.release()

    def print_error(self):
        self.print(get_traceback())


def main():
    parser = ArgumentParser(description='Personal automation script manager.')
    parser.add_argument('config_path', type=str,
                        help='Path to configuration file')
    parser.add_argument('--update_interval', type=float, default=30,
                        help='Update interval in minutes')
    args = parser.parse_args()
    caster = Caster(args.config_path, args.update_interval)
    caster.start()
    return 0


if __name__ == '__main__':
    main()
