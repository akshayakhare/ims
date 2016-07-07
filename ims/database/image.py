from sqlalchemy import Boolean, ForeignKey
from sqlalchemy import UniqueConstraint

from ims.common.log import *
from ims.database.project import *
from ims.exception import *



logger = create_logger(__name__)
# This class is responsible for doing CRUD operations on the Image Table in DB
# This class was written as per the Repository Model which allows us to change the DB in the future without changing
# business code
class ImageRepository:
    @trace
    def __init__(self, connection):
        self.connection = connection

    # inserts the arguments into table
    # Commits if inserted successfully otherwise rollbacks if some issue occured and bubbles the exception
    @log
    def insert(self, image_name, project_id, is_public=False, is_snapshot=False,
               is_provision_clone=False,
               id=None):
        try:
            img = Image()
            img.name = image_name
            img.project_id = project_id
            img.is_public = is_public
            img.is_snapshot = is_snapshot
            img.is_provision_clone = is_provision_clone
            if id is not None:
                img.id = id
            self.connection.session.add(img)
            self.connection.session.commit()
        except SQLAlchemyError as e:
            self.connection.session.rollback()
            raise db_exceptions.ORMException(e.message)

    # deletes images with name under the given project name
    # commits if deletion was successful otherwise rollback occurs and exception is bubbled up
    @log
    def delete_with_name_from_project(self, name, project_name):
        try:
            self.connection.session.query(Image). \
                filter(Image.project.has(name=project_name)).filter_by(
                name=name).delete(synchronize_session=False)
            self.connection.session.commit()
        except SQLAlchemyError as e:
            self.connection.session.rollback()
            raise db_exceptions.ORMException(e.message)

    # fetch image ids with name in project with name
    # returns a array of image ids of the images which have the given name
    @log
    def fetch_id_with_name_from_project(self, name, project_name):
        try:
            image = self.connection.session.query(Image). \
                filter(Image.project.has(name=project_name)).filter_by(
                name=name).one_or_none()
            if image is not None:
                return image.id
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)

    # Fetch the list of images which are public
    # We are returning a dictionary of format {image_name : <img_name> , project_name : <proj_name>}
    @log
    def fetch_names_with_public(self):
        try:
            img_list = self.connection.session.query(Image).filter_by(
                is_public=True)
            return [{'image_name': image.name,
                     'project_name': image.project.name}
                    for image in img_list]
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)

    # fetch the image names which are under the given project name
    # returning a list of strings
    @log
    def fetch_names_from_project(self, project_name):
        try:
            images = self.connection.session.query(Image).filter(
                Image.project.has(name=project_name))
            return [image.name for image in images]
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)

    @log
    def fetch_images_from_project(self, project_name):
        try:
            images = self.connection.session.query(Image).filter(
                Image.project.has(name=project_name)).filter_by(
                is_snapshot=False).filter_by(is_provision_clone=False)
            return [image.name for image in images]
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)

    @log
    def fetch_snapshots_from_project(self, project_name):
        try:
            images = self.connection.session.query(Image).filter(
                Image.project.has(name=project_name)).filter_by(
                is_snapshot=True)
            return [image.name for image in images]
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)

    # fetch name of image with given id
    @log
    def fetch_name_with_id(self, id):
        try:
            image = self.connection.session.query(Image).filter_by(
                id=id).one_or_none()
            if image is not None:
                return image.name
        except SQLAlchemyError as e:
            raise db_exceptions.ORMException(e.message)


# This class represents the image table
# the Column variables are the columns in the table
# the relationship variables is loaded eagerly as the session is terminated after the object is retrieved
# The snaphosts relationship is also delete on cascade (Commented)
# snapshots relationship is a reverse relation for easy traversal if required (Commented)
class Image(DatabaseConnection.Base):
    __tablename__ = "image"

    # Columns in the table
    id = Column(Integer, primary_key=True, nullable=False, autoincrement=True)
    name = Column(String, nullable=False)
    is_public = Column(Boolean, nullable=False, default=False)
    is_snapshot = Column(Boolean, nullable=False, default=False)
    is_provision_clone = Column(Boolean, nullable=False, default=False)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)

    # Relationships in the table
    # Back populates to images in Project Class and is eagerly loaded
    project = relationship("Project", back_populates="images")

    # Users should not be able to create images with same name in a given
    # project. So we are creating a unique constraint.
    __table_args__ = (UniqueConstraint("project_id", "name",
                                       name="_project_id_image_name_unique_constraint"),)

    # Removed snapshot class for now
    # snapshots = relationship("Snapshot", back_populates="image", lazy="joined", cascade="all, delete, delete-orphan")
