#!/usr/bin/python
import base64
import io
import time

from ims.database import *
from ims.einstein.ceph import *
from ims.einstein.dnsmasq import *
from ims.einstein.hil import *
from ims.einstein.iscsi import *
from ims.exception import *

logger = create_logger(__name__)


class BMI:
    @log
    def __init__(self, *args):
        if args.__len__() == 1:
            credentials = args[0]
            self.config = config.get()
            self.db = Database()
            self.__process_credentials(credentials)
            self.hil = HIL(base_url=self.config.haas_url, usr=self.username,
                           passwd=self.password)
            self.fs = RBD(self.config.fs[constants.CEPH_CONFIG_SECTION_NAME],
                          self.config.iscsi_update_password)
            self.dhcp = DNSMasq()
            self.iscsi = IET(self.fs, self.config.iscsi_update_password)
        elif args.__len__() == 3:
            username, password, project = args
            self.config = config.get()
            self.username = username
            self.password = password
            self.project = project
            self.db = Database()
            self.pid = self.__does_project_exist(self.project)
            self.is_admin = self.__check_admin()
            self.hil = HIL(base_url=self.config.haas_url, usr=self.username,
                           passwd=self.password)
            self.fs = RBD(self.config.fs[constants.CEPH_CONFIG_SECTION_NAME],
                          self.config.iscsi_update_password)
            logger.debug("Username is %s and Password is %s", self.username,
                         self.password)
            self.dhcp = DNSMasq()
            self.iscsi = IET(self.fs, self.config.iscsi_update_password)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()

    @trace
    def __does_project_exist(self, project):
        pid = self.db.project.fetch_id_with_name(project)
        # None as a query result implies that the project does not exist.
        if pid is None:
            logger.info("Raising Project Not Found Exception for %s",
                        project)
            raise db_exceptions.ProjectNotFoundException(project)

        return pid

    # this method will determine whether user is admin (still unclear on doing
    # it properly)
    def __check_admin(self):
        return True

    @trace
    def __get_ceph_image_name(self, name):
        img_id = self.db.image.fetch_id_with_name_from_project(name,
                                                               self.project)
        if img_id is None:
            logger.info("Raising Image Not Found Exception for %s", name)
            raise db_exceptions.ImageNotFoundException(name)

        return str(self.config.uid) + "img" + str(img_id)

    def get_ceph_image_name_from_project(self, name, project_name):
        img_id = self.db.image.fetch_id_with_name_from_project(name,
                                                               project_name)
        if img_id is None:
            logger.info("Raising Image Not Found Exception for %s", name)
            raise db_exceptions.ImageNotFoundException(name)

        return str(self.config.uid) + "img" + str(img_id)

    @trace
    def __extract_id(self, ceph_img_name):
        start_index = ceph_img_name.find("img")
        start_index += 3
        img_id = ceph_img_name[start_index:]
        return img_id

    @trace
    def __process_credentials(self, credentials):
        base64_str, self.project = credentials
        self.pid = self.__does_project_exist(self.project)
        self.username, self.password = tuple(
            base64.b64decode(base64_str).split(':'))
        logger.debug("Username is %s and Password is %s", self.username,
                     self.password)
        self.is_admin = self.__check_admin()

    @log
    def __register(self, node_name, img_name, target_name):
        mac_addr = "01-" + self.hil.get_node_mac_addr(node_name).replace(":",
                                                                         "-")
        logger.debug("The Mac Addr File name is %s", mac_addr)
        self.__generate_ipxe_file(node_name, target_name)
        self.__generate_mac_addr_file(img_name, node_name, mac_addr)

    @log
    def __generate_ipxe_file(self, node_name, target_name):
        template_loc = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        logger.debug("Template LOC = %s", template_loc)
        path = self.config.ipxe_loc + node_name + ".ipxe"
        logger.debug("The Path for ipxe file is %s", path)
        try:
            with io.open(path, 'w') as ipxe:
                for line in io.open(template_loc + "/ipxe.temp", 'r'):
                    line = line.replace(constants.IPXE_TARGET_NAME, target_name)
                    line = line.replace(constants.IPXE_ISCSI_IP,
                                        self.config.iscsi_ip)
                    ipxe.write(line)
            logger.info("Generated ipxe file")
            os.chmod(path, 0755)
            logger.info("Changed permissions to 755")
        except (OSError, IOError) as e:
            logger.info("Raising Registration Failed Exception for %s",
                        node_name)
            raise RegistrationFailedException(node_name, e.message)

    @log
    def __generate_mac_addr_file(self, img_name, node_name, mac_addr):
        template_loc = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        logger.debug("Template LOC = %s", template_loc)
        path = self.config.pxelinux_loc + mac_addr
        logger.debug("The Path for mac addr file is %s", path)
        try:
            with io.open(path, 'w') as mac:
                for line in io.open(template_loc + "/mac.temp", 'r'):
                    line = line.replace(constants.MAC_IMG_NAME, img_name)
                    line = line.replace(constants.MAC_IPXE_NAME,
                                        node_name + ".ipxe")
                    mac.write(line)
            logger.info("Generated mac addr file")
            os.chmod(path, 0644)
            logger.debug("Changed permissions to 644")
        except (OSError, IOError) as e:
            logger.info("Raising Registration Failed Exception for %s",
                        node_name)
            raise RegistrationFailedException(node_name, e.message)

    # Parses the Exception and returns the dict that should be returned to user
    @log
    def __return_error(self, ex):

        # Replaces the image name with id in error string
        @log
        def swap_id_with_name(err_str):
            parts = err_str.split(" ")
            start_index = parts[0].find("img")
            if start_index != -1:
                start_index += 3
                img_id = parts[0][start_index:]
                name = self.db.image.fetch_name_with_id(img_id)
                if name is not None:
                    parts[0] = name
            return " ".join(parts)

        logger.debug("Checking if FileSystemException")
        if FileSystemException in ex.__class__.__bases__:
            logger.debug("It is FileSystemException")
            return {constants.STATUS_CODE_KEY: ex.status_code,
                    constants.MESSAGE_KEY: swap_id_with_name(str(ex))}

        return {constants.STATUS_CODE_KEY: ex.status_code,
                constants.MESSAGE_KEY: str(ex)}

    # A custom function which is wrapper around only success code that
    # we are creating.
    @log
    def __return_success(self, obj):
        return {constants.STATUS_CODE_KEY: 200,
                constants.RETURN_VALUE_KEY: obj}

    @log
    def shutdown(self):
        self.fs.tear_down()
        self.db.close()

    # Provisions from HaaS and Boots the given node with given image
    @log
    def provision(self, node_name, img_name, network, nic):
        try:
            self.hil.attach_node_to_project_network(node_name, network, nic)

            parent_id = self.db.image.fetch_id_with_name_from_project(img_name,
                                                                      self.project)
            self.db.image.insert(node_name, self.pid, parent_id)
            clone_ceph_name = self.__get_ceph_image_name(node_name)
            ceph_img_name = self.__get_ceph_image_name(img_name)
            self.fs.clone(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME,
                          clone_ceph_name)
            ceph_config = self.config.fs[constants.CEPH_CONFIG_SECTION_NAME]
            logger.debug("Contents of ceph_config = %s", str(ceph_config))
            self.iscsi.create_mapping(clone_ceph_name)
            logger.info("The create command was executed successfully")
            self.__register(node_name, img_name, clone_ceph_name)
            return self.__return_success(True)

        except ISCSIException as e:
            # Message is being handled by custom formatter
            logger.exception('')
            clone_ceph_name = self.__get_ceph_image_name(node_name)
            self.fs.remove(clone_ceph_name)
            self.db.image.delete_with_name_from_project(node_name, self.project)
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.detach_node_from_project_network(node_name, network,
                                                      nic)
            return self.__return_error(e)

        except FileSystemException as e:
            # Message is being handled by custom formatter
            logger.exception('')
            self.db.image.delete_with_name_from_project(node_name, self.project)
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.detach_node_from_project_network(node_name, network,
                                                      nic)
            return self.__return_error(e)
        except DBException as e:
            # Message is being handled by custom formatter
            logger.exception('')
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.detach_node_from_project_network(node_name, network,
                                                      nic)
            return self.__return_error(e)
        except HaaSException as e:
            # Message is being handled by custom formatter
            logger.exception('')
            return self.__return_error(e)

    # This is for detach a node and removing it from iscsi
    # and destroying its image
    @log
    def deprovision(self, node_name, network, nic):
        ceph_img_name = None
        try:
            self.hil.detach_node_from_project_network(node_name,
                                                      network, nic)
            ceph_img_name = self.__get_ceph_image_name(node_name)
            self.db.image.delete_with_name_from_project(node_name, self.project)
            ceph_config = self.config.fs[constants.CEPH_CONFIG_SECTION_NAME]
            logger.debug("Contents of ceph+config = %s", str(ceph_config))
            self.iscsi.delete_mapping(ceph_img_name)
            logger.info("The delete command was executed successfully")
            ret = self.fs.remove(str(ceph_img_name).encode("utf-8"))
            return self.__return_success(ret)

        except FileSystemException as e:
            logger.exception('')
            self.iscsi.create_mapping(ceph_img_name)
            parent_name = self.fs.get_parent_info(ceph_img_name)[1]

            parent_id = self.db.image.fetch_id_with_name_from_project(
                parent_name,
                self.project)
            self.db.image.insert(node_name, self.pid, parent_id,
                                 id=self.__extract_id(ceph_img_name))
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.attach_node_to_project_network(node_name, network, nic)
            return self.__return_error(e)
        except ISCSIException as e:
            logger.exception('')
            parent_name = self.fs.get_parent_info(ceph_img_name)[1]
            parent_id = self.db.image.fetch_id_with_name_from_project(
                parent_name,
                self.project)
            self.db.image.insert(node_name, self.pid, parent_id,
                                 id=self.__extract_id(ceph_img_name))
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.attach_node_to_project_network(node_name, network, nic)
            return self.__return_error(e)
        except DBException as e:
            logger.exception('')
            time.sleep(constants.HAAS_CALL_TIMEOUT)
            self.hil.attach_node_to_project_network(node_name, network, nic)
            return self.__return_error(e)
        except HaaSException as e:
            logger.exception('')
            return self.__return_error(e)

    # Creates snapshot for the given image with snap_name as given name
    # fs_obj will be populated by decorator
    @log
    def create_snapshot(self, node_name, snap_name):
        try:
            self.hil.validate_project(self.project)

            ceph_img_name = self.__get_ceph_image_name(node_name)

            self.fs.snap_image(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME)
            parent_id = self.db.image.fetch_parent_id(self.project, node_name)
            self.db.image.insert(snap_name, self.pid, parent_id,
                                 is_snapshot=True)
            snap_ceph_name = self.__get_ceph_image_name(snap_name)
            self.fs.clone(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME,
                          snap_ceph_name)
            self.fs.flatten(snap_ceph_name)
            self.fs.snap_image(snap_ceph_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(snap_ceph_name,
                                 constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_unprotect(ceph_img_name,
                                   constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.remove_snapshot(ceph_img_name,
                                    constants.DEFAULT_SNAPSHOT_NAME)
            return self.__return_success(True)

        except (HaaSException, DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    # Lists snapshot for the given image img_name
    # URL's have to be read from BMI config file
    # fs_obj will be populated by decorator
    @log
    def list_snapshots(self):
        try:
            self.hil.validate_project(self.project)
            snapshots = self.db.image.fetch_snapshots_from_project(self.project)
            return self.__return_success(snapshots)

        except (HaaSException, DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    # Removes snapshot snap_name for the given image img_name
    # fs_obj will be populated by decorator
    @log
    def remove_image(self, img_name):
        try:
            self.hil.validate_project(self.project)
            ceph_img_name = self.__get_ceph_image_name(img_name)

            self.fs.snap_unprotect(ceph_img_name,
                                   constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.remove_snapshot(ceph_img_name,
                                    constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.remove(ceph_img_name)
            self.db.image.delete_with_name_from_project(img_name, self.project)
            return self.__return_success(True)
        except (HaaSException, DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    # Lists the images for the project which includes the snapshot
    @log
    def list_images(self):
        try:
            self.hil.validate_project(self.project)
            names = self.db.image.fetch_images_from_project(self.project)
            return self.__return_success(names)

        except (HaaSException, DBException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def list_provisioned_nodes(self):
        try:
            clones = self.db.image.fetch_clones_from_project(self.project)
            return self.__return_success(clones)
        except DBException as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def list_all_images(self):
        try:
            images = self.db.image.fetch_all_images()
            new_images = []
            for image in images:
                image.insert(3, self.get_ceph_image_name_from_project(image[1],
                                                                      image[2]))
                new_images.append(image)
            return self.__return_success(new_images)
        except DBException as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def import_ceph_image(self, img):
        try:
            ceph_img_name = str(img)

            self.fs.snap_image(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.db.image.insert(ceph_img_name, self.pid)
            snap_ceph_name = self.__get_ceph_image_name(ceph_img_name)
            self.fs.clone(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME,
                          snap_ceph_name)
            self.fs.flatten(snap_ceph_name)
            self.fs.snap_image(snap_ceph_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(snap_ceph_name,
                                 constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_unprotect(ceph_img_name,
                                   constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.remove_snapshot(ceph_img_name,
                                    constants.DEFAULT_SNAPSHOT_NAME)
            return self.__return_success(True)
        except (DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def import_ceph_snapshot(self, img, snap_name, protect):
        try:
            ceph_img_name = str(img)

            if protect:
                self.fs.snap_protect(ceph_img_name, snap_name)
            self.db.image.insert(ceph_img_name, self.pid)
            snap_ceph_name = self.__get_ceph_image_name(ceph_img_name)
            self.fs.clone(ceph_img_name, snap_name,
                          snap_ceph_name)
            self.fs.flatten(snap_ceph_name)
            self.fs.snap_image(snap_ceph_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(snap_ceph_name,
                                 constants.DEFAULT_SNAPSHOT_NAME)
            return self.__return_success(True)
        except (DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def export_ceph_image(self, img, name):
        try:
            ceph_img_name = self.__get_ceph_image_name(img)
            self.fs.clone(ceph_img_name, constants.DEFAULT_SNAPSHOT_NAME, name)
            self.fs.flatten(name)
            return self.__return_success(True)
        except FileSystemException as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def delete_image(self, project, img):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            self.db.image.delete_with_name_from_project(img, project)
            return self.__return_success(True)
        except (DBException, AuthorizationFailedException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def add_image(self, project, img, id, snap, parent, public):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            parent_id = None
            if parent is not None:
                parent_id = self.db.image.fetch_id_with_name_from_project(
                    parent,
                    project)
            pid = self.__does_project_exist(project)
            self.db.image.insert(img, pid, parent_id, public, snap, id)
            return self.__return_success(True)
        except (DBException, AuthorizationFailedException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def get_node_ip(self, node_name):
        try:
            mac_addr = self.hil.get_node_mac_addr(node_name)
            return self.dhcp.get_ip(mac_addr)
        except (HaaSException, DHCPException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def copy_image(self, img1, dest_project, img2=None):
        try:
            if not self.is_admin and (self.project != dest_project):
                raise exception.AuthorizationFailedException()
            dest_pid = self.__does_project_exist(dest_project)
            self.db.image.copy_image(self.project, img1, dest_pid, img2)
            if img2 is not None:
                ceph_name = self.__get_ceph_image_name(img2, dest_project)
            else:
                ceph_name = self.__get_ceph_image_name(img1, dest_project)
            self.fs.clone(self.__get_ceph_image_name(img1, self.project),
                          constants.DEFAULT_SNAPSHOT_NAME, ceph_name)
            self.fs.snap_image(ceph_name, constants.DEFAULT_SNAPSHOT_NAME)
            self.fs.snap_protect(ceph_name, constants.DEFAULT_SNAPSHOT_NAME)
            return self.__return_success(True)
        except (DBException, FileSystemException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def move_image(self, img1, dest_project, img2):
        try:
            if not self.is_admin and (self.project != dest_project):
                raise exception.AuthorizationFailedException()
            dest_pid = self.__does_project_exist(dest_project)
            self.db.image.move_image(self.project, img1, dest_pid, img2)
            return self.__return_success(True)
        except DBException as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def add_project(self, project, network, id):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            self.db.project.insert(project, network, id)
            return self.__return_success(True)
        except (DBException, AuthorizationFailedException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def delete_project(self, project):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            self.db.project.delete_with_name(project)
            return self.__return_success(True)
        except (DBException, AuthorizationFailedException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def list_projects(self):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            projects = self.db.project.fetch_projects()
            return self.__return_success(projects)
        except (DBException, AuthorizationFailedException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def mount_image(self, img):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            ceph_img_name = self.__get_ceph_image_name(img)
            self.iscsi.create_mapping(ceph_img_name)
            return self.__return_success(True)
        except (ISCSIException, DBException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def umount_image(self, img):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            ceph_img_name = self.__get_ceph_image_name(img)
            self.iscsi.delete_mapping(ceph_img_name)
            return self.__return_success(True)
        except (ISCSIException, DBException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def show_mounted(self):
        try:
            if not self.is_admin:
                raise exception.AuthorizationFailedException()
            mappings = self.iscsi.show_mappings()
            swapped_mappings = {}
            for k, v in mappings.iteritems():
                img_id = self.__extract_id(k)
                if self.project == self.db.image.fetch_project_with_id(img_id):
                    swapped_mappings[
                        self.db.image.fetch_name_with_id(img_id)] = v
            return self.__return_success(swapped_mappings)
        except (ISCSIException, DBException) as e:
            logger.exception('')
            return self.__return_error(e)

    @log
    def remake_mappings(self):
        try:
            self.iscsi.remake_mappings()
        except (FileSystemException, ISCSIException) as e:
            logger.exception('')
