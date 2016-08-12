import subprocess

import re
import sh

import ims.common.constants as constants
from ims.common.log import *
from ims.exception import *

logger = create_logger(__name__)


class IET:
    @log
    def __init__(self, fs, password):
        self.fs = fs
        self.password = password

    @log
    def create_mapping(self, ceph_img_name):
        rbd_name = None
        try:
            mappings = self.show_mappings()
            if ceph_img_name in mappings:
                raise iscsi_exceptions.NodeAlreadyInUseException()
            rbd_name = self.fs.map(ceph_img_name)
            self.__add_mapping(ceph_img_name, rbd_name)
            self.__restart()
            self.__check_status(True)
        except iscsi_exceptions.UpdateConfigFailedException as e:
            maps = self.fs.showmapped()
            self.fs.unmap(maps[ceph_img_name])
            raise e
        except (iscsi_exceptions.MountException,
                iscsi_exceptions.DuplicatesException,
                iscsi_exceptions.RestartFailedException) as e:
            self.__remove_mapping(ceph_img_name, rbd_name)
            maps = self.fs.showmapped()
            self.fs.unmap(maps[ceph_img_name])
            raise e

    @log
    def delete_mapping(self, ceph_img_name):
        mappings = None
        try:
            iscsi_mappings = self.show_mappings()
            if ceph_img_name not in iscsi_mappings:
                raise iscsi_exceptions.NodeAlreadyUnmappedException()
            self.__stop()
            self.__check_status(False)
            mappings = self.fs.showmapped()
            self.__remove_mapping(ceph_img_name, mappings[ceph_img_name])
            self.fs.unmap(mappings[ceph_img_name])
            self.__restart()
            self.__check_status(True)
        except iscsi_exceptions.UpdateConfigFailedException as e:
            self.__restart()
            raise e
        except file_system_exceptions.UnmapFailedException as e:
            self.__add_mapping(ceph_img_name, mappings(ceph_img_name))
            self.__restart()
            raise e
        except (iscsi_exceptions.MountException,
                iscsi_exceptions.DuplicatesException,
                iscsi_exceptions.RestartFailedException) as e:
            self.fs.map(ceph_img_name)
            self.__add_mapping(ceph_img_name, mappings(ceph_img_name))
            self.__restart()
            raise e

    @log
    def show_mappings(self):
        mappings = {}
        try:
            with open(constants.IET_ISCSI_CONFIG_LOC, 'r') as fi:
                target = None
                for line in fi:
                    line = line.strip()
                    if line.startswith(constants.IET_TARGET_STARTING):
                        if target is None:
                            target = line.split('.')[2]
                        else:
                            raise iscsi_exceptions.InvalidConfigException()
                    elif line.startswith(constants.IET_LUN_STARTING):
                        if target is not None:
                            mappings[target] = line.split(',')[0].split('=')[1]
                            target = None
                        else:
                            raise iscsi_exceptions.InvalidConfigException()

            return mappings
        except IOError as e:
            logger.info("Raising Read Config Failed Exception")
            raise iscsi_exceptions.ReadConfigFailedException(e.message)

    @log
    def __add_mapping(self, ceph_img_name, rbd_name):
        try:
            with open(constants.IET_ISCSI_CONFIG_LOC, 'a') as fi:
                fi.write(
                    constants.IET_MAPPING_TEMP.replace(constants.CEPH_IMG_NAME,
                                                       ceph_img_name).replace(
                        constants.RBD_NAME, rbd_name))
        except IOError as e:
            logger.info("Raising Update Config Failed Exception")
            raise iscsi_exceptions.UpdateConfigFailedException(e.message)

    @log
    def __remove_mapping(self, ceph_img_name, rbd_name):
        try:
            with open(constants.IET_ISCSI_CONFIG_LOC, 'r') as fi:
                with open(constants.IET_ISCSI_CONFIG_TEMP_LOC, 'w') as temp:
                    for line in fi:
                        if line.find(ceph_img_name) == -1 and line.find(
                                rbd_name) == -1:
                            temp.write(line)
            os.rename(constants.IET_ISCSI_CONFIG_TEMP_LOC,
                      constants.IET_ISCSI_CONFIG_LOC)
        except IOError as e:
            logger.info("Raising Update Config Failed Exception")
            raise iscsi_exceptions.UpdateConfigFailedException(e.message)

    @log
    def __restart(self):
        command = "echo {0} | sudo -S service iscsitarget restart".format(
            self.password)
        p = subprocess.Popen(command, shell=True, stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE)
        output, err = p.communicate()
        # output = sh.service.iscsitarget.restart()
        if p.returncode == 0:
            return output.strip()
        else:
            logger.info("Raising Restart Failed Exception")
            raise iscsi_exceptions.RestartFailedException()

    @log
    def __stop(self):
        command = "echo {0} | sudo -S service iscsitarget stop".format(
            self.password)
        p = subprocess.Popen(command, shell=True, stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE)
        output, err = p.communicate()
        # output = sh.service.iscsitarget.stop()
        if p.returncode == 0:
            return output.strip()
        else:
            logger.info("Raising Stop Failed Exception")
            raise iscsi_exceptions.StopFailedException()

    def __check_status(self, on):
        output = sh.service.iscsitarget.status(_ok_code=[0, 3])
        ansi_escape = re.compile(r'\x1b[^m]*m')
        output = ansi_escape.sub('', output.strip())
        parts = output.split("\n")
        active = not on
        targets = []
        failed_mount = []
        duplicates = []
        for part in parts:
            if part.strip().startswith("Active"):
                line_parts = part.strip().split()
                if line_parts[1] + line_parts[2] == "active(running)":
                    active = True
                if line_parts[1] + line_parts[2] == "inactive(dead)":
                    active = False
            elif part.strip().find("created target") != -1:
                line = part.strip()[
                       part.strip().find("created target"):].split()
                targets.append(line[2].split(".")[2])
            elif part.strip().find("unable to create logical unit") != -1:
                target = targets.pop()
                failed_mount.append(target)
            elif part.strip().find("duplicated target") != -1:
                line = part.strip()[
                       part.strip().find("duplicated target"):].split()
                duplicates.append(line[2].split(".")[2])

        if failed_mount:
            logger.info("Raising Mount Exception for %s", failed_mount)
            raise iscsi_exceptions.MountException(failed_mount)

        if duplicates:
            logger.info("Raising Mount Exception for %s", duplicates)
            raise iscsi_exceptions.DuplicatesException(duplicates)

        if not active and on:
            logger.info("Raising Restart Failed Exception")
            raise iscsi_exceptions.RestartFailedException()
        elif not on and active:
            logger.info("Raising Stop Failed Exception")
            raise iscsi_exceptions.StopFailedException()

    def remake_mappings(self):

        if not self.fs.showmapped():
            return

        mappings = self.show_mappings()

        for k, v in mappings.items():
            self.__remove_mapping(k, v)

        for k, v in mappings.items():
            rbd_name = self.fs.map(k)
            self.__add_mapping(k, rbd_name)

        self.__restart()
