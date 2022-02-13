# -*- coding: utf-8 -*-
#
# Copyright © by Christof Küstner

"""
Hotify - Creates hot folder environments based on configuration file

@author: kuestner
"""

# ==============================================================================
# IMPORT
# ==============================================================================
from typing import List, Union
import os
import sys
from pathlib import Path
import yaml
import subprocess
import shlex
import time
import click
import shutil
import logging
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
import threading
from queue import Queue


# ==============================================================================
# CONSTANTS
# ==============================================================================
HOTIFY_CONFIG = Path(__file__).parent / r"hotify.yml"
FILE_MODIFICATION_FINISHED_DELAY = 0.2


# ==============================================================================
# INITS
# ==============================================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("fsevents").disabled = True


# ==============================================================================
# DEFINITION
# ==============================================================================
class SetQueue(Queue):
    def _init(self, maxsize):
        self.queue = set()

    def _put(self, item):
        self.queue.add(item)

    def _get(self):
        return self.queue.pop()

    def get_all(self):
        result_list = []
        while not self.empty():
            result_list.append(self.get())

        return result_list

    def __iter__(self):
        return self.queue.__iter__()

    def __next__(self):
        return self.queue.__next__()

    def __len__(self):
        return self.queue.__len__()


class HotifyEventHandler(PatternMatchingEventHandler):
    def __init__(
        self, in_pattern, output_folder, multiple_files_delay, trigger, *args, **kwargs
    ):
        PatternMatchingEventHandler.__init__(
            self,
            patterns=in_pattern,
            ignore_patterns="",
            ignore_directories=True,
            case_sensitive=False,
            *args,
            **kwargs,
        )

        # store parameter
        self._output_folder = output_folder
        self._trigger = trigger
        self._multiple_files_delay = multiple_files_delay
        self._multiple_input_files_trigger = "in_files" in self._trigger

        # handle in_files which defines the trigger to wait for multiple files,
        # i.e. delay processing until the folder rested for hotify_input_multiple_files_delay
        self._multiple_input_files_queue = None
        self._multiple_files_delay_thread = None
        if self._multiple_input_files_trigger:
            self._multiple_files_queue = SetQueue()
            self._multiple_files_delay_thread = threading.Thread(
                target=self._delay_trigger,
                args=(),
                daemon=True,
            )
            self._multiple_files_delay_thread.start()

    def _execute_trigger(self, input_file_paths: Union[Path, list]):
        if self._multiple_input_files_trigger:  # multiple files as input
            output_file_path = (
                self._output_folder / f"multiple--{input_file_paths[0].name}"
            )
            in_files_arg = " ".join(
                f'"{file_path.absolute()}"' for file_path in input_file_paths
            )
            trigger_bin_and_args = shlex.split(
                self._trigger.format(
                    in_files=in_files_arg,
                    out_file=output_file_path.absolute(),
                )
            )
        else:
            output_file_path = self._output_folder / input_file_paths.name
            trigger_bin_and_args = shlex.split(
                self._trigger.format(
                    in_file=input_file_paths.absolute(),
                    out_file=output_file_path.absolute(),
                )
            )
        # run trigger and handle errors
        logging.debug(f"EXECUTE-TRIGGER: {trigger_bin_and_args}")
        try:
            # TODO: Run trigger in background (in case of compute intensive operations)
            trigger_process = subprocess.run(
                trigger_bin_and_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logging.error(
                f"EXECUTE-TRIGGER (FAILED: '{str(e)}'): {trigger_bin_and_args}"
            )
        finally:
            if trigger_process.returncode > 1:
                logging.error(
                    f"EXECUTE-TRIGGER (FAILED: returncode > 1): {trigger_bin_and_args}"
                )
            else:
                # TODO: Clean input files, after it was successful
                pass

    def _delay_trigger(self):
        q = self._multiple_files_queue
        trigger = self._execute_trigger
        delay = self._multiple_files_delay

        while True:
            if not q.empty():
                all_input_files_finished = all(
                    [
                        abs(time.time() - file_path.stat().st_ctime) > delay
                        for file_path in q
                    ]
                )
                if all_input_files_finished:
                    all_input_files = q.get_all()
                    trigger(input_file_paths=all_input_files)
            time.sleep(1.0)

    def _wait_until_file_modification_finished(self, file_path: Path):
        historical_size = -1
        while historical_size != file_path.stat().st_size:
            historical_size = file_path.stat().st_size
            time.sleep(FILE_MODIFICATION_FINISHED_DELAY)
        logging.debug(f"FILE MODIFICATION FINISHED: {file_path.absolute()}")

    def on_created(self, event):
        file_created_path = Path(event.src_path)
        logging.debug(f"FILE CREATED: {file_created_path}")
        self._wait_until_file_modification_finished(file_created_path)
        if self._multiple_input_files_trigger:  # multiple files as input
            self._multiple_files_queue.put(file_created_path)
        else:
            self._execute_trigger(input_file_paths=file_created_path)

    def on_modified(self, event):
        file_modified_path = Path(event.src_path)
        logging.debug(f"FILE MODIFIED: {file_modified_path}")
        self._wait_until_file_modification_finished(file_modified_path)
        if self._multiple_input_files_trigger:  # multiple files as input
            self._multiple_files_queue.put(file_modified_path)
        else:
            self._execute_trigger(input_file_paths=file_modified_path)


class HotifyObserver(Observer):
    def __init__(
        self,
        hotify_hot_folder: Path,
        hotify_output_folder: Path,
        hotify_input_multiple_files_delay: float,
        hotify_envs: dict,
        *args,
        **kwargs,
    ):
        # init Observer super
        Observer.__init__(self, *args, **kwargs)

        # store parameter
        self._hotify_hot_folder = hotify_hot_folder
        self._hotify_output_folder = hotify_output_folder
        self._hotify_input_multiple_files_delay = hotify_input_multiple_files_delay
        self._hotify_envs = hotify_envs

        # register environments
        self.register_environments()

    def register_environments(self, recursively: bool = False):
        # register all environments in hotify_envs
        for hotify_env in self._hotify_envs:
            env_name = hotify_env["name"]
            env_triggers = hotify_env["trigger"]
            env_in_pattern = hotify_env["in_pattern"]

            hotify_event_path = self._hotify_hot_folder / env_name
            hotify_event_path.mkdir(parents=True, exist_ok=True)

            if isinstance(env_triggers, str):  # single trigger
                self._register_trigger(
                    hotify_event_path,
                    env_in_pattern,
                    self._hotify_output_folder,
                    env_triggers,
                    recursively,
                )
            else:  # trigger chain
                for step_i, trigger_i in enumerate(env_triggers):
                    # define output folder
                    if step_i < len(env_triggers) - 1:
                        hotify_event_output_path_i = (
                            hotify_event_path / f"step_{step_i+1:03d}"
                        )
                        hotify_event_output_path_i.mkdir(parents=True, exist_ok=True)
                    else:
                        hotify_event_output_path_i = self._hotify_output_folder

                    # first step will become the landing folder (default)
                    if step_i == 0:
                        self._register_trigger(
                            hotify_event_path,
                            env_in_pattern,
                            hotify_event_output_path_i,
                            trigger_i,
                            False,
                        )
                    else:  # all following steps will be separated in order not to trigger default step again
                        hotify_event_path_i = hotify_event_path / f"step_{step_i:03d}"
                        hotify_event_path_i.mkdir(parents=True, exist_ok=True)
                        self._register_trigger(
                            hotify_event_path_i,
                            ["*.*"],
                            hotify_event_output_path_i,
                            trigger_i,
                            False,
                        )

    def _register_trigger(
        self,
        event_path: Path,
        in_pattern: List[str],
        output_folder: Path,
        trigger: Union[str, List[str]],
        recursively: bool,
    ):
        hotify_event_handler = HotifyEventHandler(
            in_pattern=in_pattern,
            output_folder=output_folder,
            multiple_files_delay=self._hotify_input_multiple_files_delay,
            trigger=trigger,
        )
        self.schedule(hotify_event_handler, event_path, recursive=recursively)

    def observe(self, initial_run: bool = True, clean_on_exit: bool = True):
        self.start()

        # initial run by touching all files
        if initial_run:
            for folder_item in self._hotify_hot_folder.glob("**/*"):
                if (
                    folder_item.is_file()
                    and folder_item.parent != self._hotify_hot_folder
                ):
                    folder_item.touch()

        # do continuously
        try:
            while True:
                time.sleep(0.5)
        finally:
            # stop observers
            self.stop()
            self.join()

            # clean hot cmd folder
            if clean_on_exit:
                logging.debug(
                    f"CLEANING hotify folder '{self._hotify_hot_folder.absolute()}'!"
                )
                shutil.rmtree(self._hotify_hot_folder.absolute())


@click.command()
@click.argument(
    "base_path",
    required=False,
    default=Path("."),
    type=click.Path(
        file_okay=False, dir_okay=True, resolve_path=True, allow_dash=False, exists=True
    ),
)
@click.option(
    "-c",
    "--clean",
    "clean_on_exit",
    is_flag=True,
    default=False,
    help="clean: clean folder on exit",
)
def hotify(base_path: Path = Path("."), clean_on_exit=False):
    """
    Creates hot folder environments in BASE_PATH based on
    configuration file (hotify.yml) with defined shell commands.
    """
    # open and double-check hotify config file
    with open(HOTIFY_CONFIG.absolute(), "r") as hotify_config_file:
        hotify_config = yaml.safe_load(hotify_config_file)

    assert "hotify_hot_folder_name" in hotify_config
    assert "hotify_output_folder_name" in hotify_config
    assert "hotify_input_multiple_files_delay" in hotify_config
    assert "hotify_environments" in hotify_config

    # get and generate temporary hotify root/base folder
    hotify_hot_folder = Path(base_path) / hotify_config["hotify_hot_folder_name"]
    hotify_hot_folder.mkdir(parents=True, exist_ok=True)

    # get and generate output folder
    hotify_output_folder = Path(base_path) / hotify_config["hotify_output_folder_name"]
    hotify_output_folder.mkdir(parents=True, exist_ok=True)

    # read delay in case of "in_files", i.e. multiple files as input for a cmd
    hotify_input_multiple_files_delay = hotify_config[
        "hotify_input_multiple_files_delay"
    ]

    # init hotify observer and its environments from config
    hotify_observer = HotifyObserver(
        hotify_hot_folder,
        hotify_output_folder,
        hotify_input_multiple_files_delay,
        hotify_config["hotify_environments"],
    )

    # observe
    hotify_observer.observe(clean_on_exit=clean_on_exit)


if __name__ == "__main__":
    hotify()
