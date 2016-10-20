import logging
import os

from django.db import models
from modularodm import Q

from addons.base.models import BaseStorageAddon, BaseNodeSettings
from osf.exceptions import NodeStateError, InvalidTagError, TagNotFoundError
from osf.models import File
from osf.models import FileNode, Guid, TrashedFileNode
from osf.models import FileVersion
from osf.models import Folder
from osf.utils.auth import Auth
from website.files import utils as files_utils
from website.files import exceptions
from website.util import permissions

from django.apps import apps
settings = apps.get_app_config('addons_osfstorage')

logger = logging.getLogger(__name__)


class OsfStorageFileNode(FileNode):
    provider = 'osfstorage'

    @classmethod
    def get(cls, _id, node):
        return cls.find_one(Q('_id', 'eq', _id) & Q('node', 'eq', node))

    @classmethod
    def get_or_create(cls, node, path):
        """Override get or create for osfstorage
        Path is always the _id of the osfstorage filenode.
        Use load here as its way faster than find.
        Just manually assert that node is equal to node.
        """
        inst = cls.load(path.strip('/'))
        # Use _id as odms default comparison mucks up sometimes
        if inst and inst.node._id == node._id:
            return inst

        # Dont raise anything a 404 will be raised later
        return cls.create(node=node, path=path)

    @classmethod
    def get_file_guids(cls, materialized_path, provider, node=None):
        guids = []
        path = materialized_path.strip('/')
        file_obj = cls.load(path)
        if not file_obj:
            file_obj = TrashedFileNode.load(path)

        # At this point, file_obj may be an OsfStorageFile, an OsfStorageFolder, or a
        # TrashedFileNode. TrashedFileNodes do not have *File and *Folder subclasses, since
        # only osfstorage trashes folders. To search for children of TrashFileNodes
        # representing ex-OsfStorageFolders, we will reimplement the `children` method of the
        # Folder class here.
        if not file_obj.is_file:
            children = []
            if isinstance(file_obj, TrashedFileNode):
                children = TrashedFileNode.find(Q('parent', 'eq', file_obj._id))
            else:
                children = file_obj.children

            for item in children:
                guids.extend(cls.get_file_guids(item.path, provider, node=node))
        else:
            try:
                guid = Guid.find(Q('referent', 'eq', file_obj))[0]
            except IndexError:
                guid = None
            if guid:
                guids.append(guid._id)

        return guids

    @property
    def kind(self):
        return 'file' if self.is_file else 'folder'

    @property
    def materialized_path(self):
        """creates the full path to a the given filenode
        Note: Possibly high complexity/ many database calls
        USE SPARINGLY
        """
        if not self.parent:
            return '/'

        # Note: ODM cache can be abused here
        # for highly nested folders calling
        # list(self.__class__.find(Q(nodesetting),Q(folder))
        # may result in a massive increase in performance

        def lineage():
            current = self
            while current:
                yield current
                current = current.parent

        path = os.path.join(*reversed([x.name for x in lineage()]))
        if self.is_file:
            return '/{}'.format(path)
        return '/{}/'.format(path)

    @property
    def path(self):
        """Path is dynamically computed as storedobject.path is stored
        as an empty string to make the unique index work properly for osfstorage
        """
        return '/' + self._id + ('' if self.is_file else '/')

    @property
    def is_checked_out(self):
        return self.checkout is not None

    def delete(self, user=None, parent=None):
        if self.node.preprint_file == self:
            self.node._is_preprint_orphan = True
            self.node.save()
        if self.is_checked_out:
            raise exceptions.FileNodeCheckedOutError()
        return super(OsfStorageFileNode, self).delete(user=user, parent=parent)

    def move_under(self, destination_parent, name=None):
        if self.is_checked_out:
            raise exceptions.FileNodeCheckedOutError()
        return super(OsfStorageFileNode, self).move_under(destination_parent, name)

    def check_in_or_out(self, user, checkout, save=False):
        """
        Updates self.checkout with the requesting user or None,
        iff user has permission to check out file or folder.
        Adds log to self.node.


        :param user:        User making the request
        :param checkout:    Either the same user or None, depending on in/out-checking
        :param save:        Whether or not to save the user
        """
        from osf.models import NodeLog  # Avoid circular import

        if (
                self.is_checked_out and self.checkout != user and permissions.ADMIN not in self.node.permissions.get(
                user._id, [])) \
                or permissions.WRITE not in self.node.get_permissions(user):
            raise exceptions.FileNodeCheckedOutError()

        action = NodeLog.CHECKED_OUT if checkout else NodeLog.CHECKED_IN

        if self.is_checked_out and action == NodeLog.CHECKED_IN or not self.is_checked_out and action == NodeLog.CHECKED_OUT:
            self.checkout = checkout

            self.node.add_log(
                action=action,
                params={
                    'kind': self.kind,
                    'project': self.node.parent_id,
                    'node': self.node._id,
                    'urls': {
                        # web_url_for unavailable -- called from within the API, so no flask app
                        'download': '/project/{}/files/{}/{}/?action=download'.format(self.node._id,
                                                                                      self.provider,
                                                                                      self._id),
                        'view': '/project/{}/files/{}/{}'.format(self.node._id, self.provider, self._id)},
                    'path': self.materialized_path
                },
                auth=Auth(user),
            )

            if save:
                self.save()

    def save(self):
        self.path = ''
        self.materialized_path = ''
        return super(OsfStorageFileNode, self).save()


class OsfStorageFile(OsfStorageFileNode, File):
    def touch(self, bearer, version=None, revision=None, **kwargs):
        try:
            return self.get_version(revision or version)
        except ValueError:
            return None

    @property
    def history(self):
        return [v.metadata for v in self.versions]

    def serialize(self, include_full=None, version=None):
        ret = super(OsfStorageFile, self).serialize()
        if include_full:
            ret['fullPath'] = self.materialized_path

        version = self.get_version(version)
        return dict(
            ret,
            version=len(self.versions),
            md5=version.metadata.get('md5') if version else None,
            sha256=version.metadata.get('sha256') if version else None,
        )

    def create_version(self, creator, location, metadata=None):
        latest_version = self.get_version()
        version = FileVersion(identifier=len(self.versions) + 1, creator=creator, location=location)

        if latest_version and latest_version.is_duplicate(version):
            return latest_version

        if metadata:
            version.update_metadata(metadata)

        version._find_matching_archive(save=False)

        version.save()
        self.versions.append(version)
        self.save()

        return version

    def get_version(self, version=None, required=False):
        if version is None:
            if self.versions:
                return self.versions[-1]
            return None

        try:
            return self.versions[int(version) - 1]
        except (IndexError, ValueError):
            if required:
                raise exceptions.VersionNotFoundError(version)
            return None

    def add_tag_log(self, action, tag, auth):
        node = self.node
        node.add_log(
            action=action,
            params={
                'parent_node': node.parent_id,
                'node': node._id,
                'urls': {
                    'download': '/project/{}/files/osfstorage/{}/?action=download'.format(node._id, self._id),
                    'view': '/project/{}/files/osfstorage/{}/'.format(node._id, self._id)},
                'path': self.materialized_path,
                'tag': tag,
            },
            auth=auth,
        )

    def add_tag(self, tag, auth, save=True, log=True):
        from osf.models import Tag, NodeLog  # Prevent import error

        if tag not in self.tags and not self.node.is_registration:
            new_tag = Tag.load(tag)
            if not new_tag:
                new_tag = Tag(_id=tag)
            new_tag.save()
            self.tags.append(new_tag)
            if log:
                self.add_tag_log(NodeLog.FILE_TAG_ADDED, tag, auth)
            if save:
                self.save()
            return True
        return False

    def remove_tag(self, tag, auth, save=True, log=True):
        from osf.models import Tag, NodeLog  # Prevent import error

        if self.node.is_registration:
            # Can't perform edits on a registration
            raise NodeStateError

        tag = Tag.load(tag)
        if not tag:
            raise InvalidTagError
        elif tag not in self.tags:
            raise TagNotFoundError
        else:
            self.tags.remove(tag)
            if log:
                self.add_tag_log(NodeLog.FILE_TAG_REMOVED, tag._id, auth)
            if save:
                self.save()
            return True

    def delete(self, user=None, parent=None):
        from website.search import search

        search.update_file(self, delete=True)
        return super(OsfStorageFile, self).delete(user, parent)

    def save(self, skip_search=False):
        from website.search import search

        ret = super(OsfStorageFile, self).save()
        if not skip_search:
            search.update_file(self)
        return ret


class OsfStorageFolder(OsfStorageFileNode, Folder):
    @property
    def is_checked_out(self):
        if self.checkout:
            return True
        for child in self.children:
            if child.is_checked_out:
                return True
        return False

    def serialize(self, include_full=False, version=None):
        # Versions just for compatibility
        ret = super(OsfStorageFolder, self).serialize()
        if include_full:
            ret['fullPath'] = self.materialized_path
        return ret


class NodeSettings(BaseStorageAddon, BaseNodeSettings):
    # Required overrides
    complete = True
    has_auth = True

    root_node = models.ForeignKey('osf.StoredFileNode', null=True, blank=True)

    @property
    def folder_name(self):
        return self.root_node.name

    def get_root(self):
        return self.root_node.wrapped()

    def on_add(self):
        if self.root_node:
            return

        # A save is required here to both create and attach the root_node
        # When on_add is called the model that self refers to does not yet exist
        # in the database and thus odm cannot attach foreign fields to it
        self.save()
        # Note: The "root" node will always be "named" empty string
        root = OsfStorageFolder(name='', node=self.owner)
        root.save()
        self.root_node = root.stored_object
        self.save()

    def after_fork(self, node, fork, user, save=True):
        clone = self.clone()
        clone.owner = fork
        clone.save()
        if not self.root_node:
            self.on_add()

        clone.root_node = files_utils.copy_files(self.get_root(), clone.owner).stored_object
        clone.save()

        return clone, None

    def after_register(self, node, registration, user, save=True):
        clone = self.clone()
        clone.owner = registration
        clone.on_add()
        clone.save()

        return clone, None

    def serialize_waterbutler_settings(self):
        return dict(settings.WATERBUTLER_SETTINGS, **{
            'nid': self.owner._id,
            'rootId': self.root_node._id,
            'baseUrl': self.owner.api_url_for(
                'osfstorage_get_metadata',
                _absolute=True,
            )
        })

    def serialize_waterbutler_credentials(self):
        return settings.WATERBUTLER_CREDENTIALS

    def create_waterbutler_log(self, auth, action, metadata):
        url = self.owner.web_url_for(
            'addon_view_or_download_file',
            path=metadata['path'],
            provider='osfstorage'
        )

        self.owner.add_log(
            'osf_storage_{0}'.format(action),
            auth=auth,
            params={
                'node': self.owner._id,
                'project': self.owner.parent_id,

                'path': metadata['materialized'],

                'urls': {
                    'view': url,
                    'download': url + '?action=download'
                },
            },
        )
