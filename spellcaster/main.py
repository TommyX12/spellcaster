from argparse import ArgumentParser
from enum import Enum
from spellcaster.util import RepeatedTimer, get_traceback
import json
import os
import subprocess
import threading


class SpellConfig(object):

    def __init__(self, id, config):
        self.id = id
        self.name = config.get('name', 'Unknown')
        self.command = config.get('command', None)
        if self.command is None:
            raise ValueError('Spell "{}" has no command'.format(self.name))


class CasterConfig(object):

    def __init__(self, config):
        self.spell_configs = {}
        spell_configs = config.get('spell_configs')
        if spell_configs is not None:
            self.spell_configs = {
                id: SpellConfig(id, spell_configs[id])
                for id in spell_configs
            }


class SpellState(object):

    def __init__(self, id, config=None):
        if config is None:
            config = {}

        self.id = id
        self.last_run = config.get('last_run', 0)

    def to_json(self):
        return {
            'last_run': self.last_run,
        }


class CasterState(object):

    def __init__(self, config=None):
        if config is None:
            config = {}

        self.spell_states = {}
        spell_states = config.get('spell_states')
        if spell_states is not None:
            self.spell_states = {
                id: SpellState(id, spell_states[id])
                for id in spell_states
            }

    def to_json(self):
        return {
            'spell_states': {
                id: self.spell_states[id].to_json()
                for id in self.spell_states
            }
        }


class SpellStatus(Enum):
    STANDBY = 0
    RUNNING = 1
    SUCCESS = 2
    ERROR = 3


class Spell(object):

    def __init__(self, config, caster):
        self.id = config.id
        self.config = config
        self.caster = caster
        self.status = SpellStatus.STANDBY

    def is_running(self):
        return self.status == SpellStatus.RUNNING

    def update(self):
        raise NotImplementedError
        with self.caster.lock_states() as spell_states:
            spell_states[self.id].last_run = 0

        self.caster.save_states()


class Caster(object):

    class AcquireLock(object):
        def __init__(self, caster, lock):
            self.caster = caster
            self.lock = lock

        def __enter__(self):
            self.lock.acquire()
            return self.caster.caster_state.spell_states

        def __exit__(self, type, value, traceback):
            self.lock.release()

    def __init__(self,
                 config_path,
                 state_path,
                 update_interval):
        self.print_lock.Lock = threading.Lock()
        self.state_lock.Lock = threading.Lock()
        self.config_path = config_path
        self.state_path = state_path
        self.caster_config = None
        self.caster_state = None
        self.spells = {}
        self.timer = RepeatedTimer(
            update_interval * 60,
            self.update
        )

    def read_config(self):
        with open(self.config_path, 'r') as config_file:
            self.caster_config = CasterConfig(json.loads(config_file.read()))

    def read_state(self):
        if os.path.exists(self.state_path):
            if not os.path.isfile(self.state_path):
                raise ValueError('{} is not a file'.format(self.state_path))

            with open(self.state_path, 'r') as state_file:
                self.caster_state = CasterState(json.loads(state_file.read()))

        self.caster_state = CasterState()

    def lock_states(self):
        return Caster.AcquireLock(self, self.state_lock)

    def update(self):
        try:
            self.read_config()
            spell_states = self.caster_state.spell_states
            for spell_config in self.caster_config.spell_configs:
                try:
                    if spell_config.id in self.spells:
                        continue

                    if spell_config.id not in spell_states:
                        spell_states[spell_config.id] = SpellState(
                            spell_config.id)

                    self.spells[spell_config.id] = Spell(spell_config)

                except Exception:
                    self.print_error()

            for id in self.spells:
                spell = self.spells[id]
                try:
                    spell.update()
                except Exception:
                    self.print_error()

        except Exception:
            self.print_error()

    def handle_request(self, request):
        try:
            self.print(request)
        except Exception:
            self.print_error()

    def start(self):
        self.read_state()
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

    def save_states(self):
        with self.lock_states():
            with open(self.state_path, 'w') as state_file:
                state_file.write(json.dumps(
                    self.caster_state.to_json(), indent=2))


def main():
    parser = ArgumentParser(description='Personal automation script manager.')
    parser.add_argument('config_path', type=str,
                        help='Path to configuration file')
    parser.add_argument('save_path', type=str,
                        help='Path to saved state file')
    parser.add_argument('--update_interval', type=float, default=60,
                        help='Update interval in minutes')
    args = parser.parse_args()
    caster = Caster(args.config_path, args.state_path, args.update_interval)
    caster.start()
    return 0


if __name__ == '__main__':
    main()
