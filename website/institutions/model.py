from django.core.urlresolvers import reverse
from django.db.models.query import QuerySet as DjangoQuerySet

from modularodm import Q
from modularodm.exceptions import NoResultsFound
from modularodm.query.query import RawQuery
from framework.mongo.storage import MongoQuerySet


class AffiliatedInstitutionsList(list):
    '''
    A list to implement append and remove methods to a private node list through a
    public Institution-returning property. Initialization should occur with the instance of the public list,
    the object the list belongs to, and the private attribute ( a list) the public property
    is attached to, and as the return value of the property.
     Ex:
     class Node():
        _affiliated_institutions = []

        @property
        affiliated_institutions(self):
            return AffiliatedInstitutionsList(
                [Institution(node) for node in self._affiliated_institutions],
                obj=self, private_target='_affiliated_institutions')
            )
    '''
    def __init__(self, init, obj, private_target):
        super(AffiliatedInstitutionsList, self).__init__(init or [])
        self.obj = obj
        self.target = private_target

    def append(self, to_append):
        temp_list = getattr(self.obj, self.target)
        temp_list.append(to_append.node)
        setattr(self.obj, self.target, temp_list)

    def remove(self, to_remove):
        temp_list = getattr(self.obj, self.target)
        temp_list.remove(to_remove.node)
        setattr(self.obj, self.target, temp_list)


class InstitutionQuerySet(MongoQuerySet):

    def __init__(self, queryset):
        if isinstance(queryset, DjangoQuerySet):
            model = queryset.model
            data = queryset.all()
        else:
            model = queryset.schema
            data = queryset.data
        super(InstitutionQuerySet, self).__init__(model, data)

    def sort(self, *field_names):
        actual_field_names = [Institution.attribute_map.get(each, each) for each in field_names]
        return super(InstitutionQuerySet, self).sort(*actual_field_names)

    def __iter__(self):
        for each in super(InstitutionQuerySet, self).__iter__():
            yield Institution(each)

    def _do_getitem(self, index):
        item = super(InstitutionQuerySet, self)._do_getitem(index)
        if isinstance(item, MongoQuerySet):
            return self.__class__(item)
        return Institution(item)

class Institution(object):
    '''
    "wrapper" class for Node. Together with the find and institution attributes & methods in Node,
    this is to be used to allow interaction with Institutions, which are Nodes (with ' institution_id ' != None),
    as if they were a wholly separate collection. To find an institution, use the find methods here,
    and to use a Node as Institution, instantiate an Institution with ' Institution(node) '
    '''
    attribute_map = {
        '_id': 'institution_id',
        'auth_url': 'institution_auth_url',
        'logout_url': 'institution_logout_url',
        'domains': 'institution_domains',
        'name': 'title',
        'logo_name': 'institution_logo_name',
        'description': 'description',
        'email_domains': 'institution_email_domains',
        'banner_name': 'institution_banner_name',
        'is_deleted': 'is_deleted',
    }

    def __init__(self, node=None):
        self.node = node
        if node is None:
            return
        for key, value in self.attribute_map.iteritems():
            setattr(self, key, getattr(node, value))

    def __getattr__(self, item):
        return getattr(self.node, item)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self._id == other._id

    def save(self):
        from website.search.search import update_institution
        update_institution(self)
        for key, value in self.attribute_map.iteritems():
            if getattr(self, key) != getattr(self.node, value):
                setattr(self.node, value, getattr(self, key))
        self.node.save()

    @classmethod
    def find(cls, query=None, deleted=False, **kwargs):
        from website.models import Node  # done to prevent import error
        if query and getattr(query, 'nodes', False):
            for node in query.nodes:
                replacement_attr = cls.attribute_map.get(node.attribute, False)
                node.attribute = replacement_attr or node.attribute
        elif isinstance(query, RawQuery):
            replacement_attr = cls.attribute_map.get(query.attribute, False)
            query.attribute = replacement_attr or query.attribute
        query = query & Q('institution_id', 'ne', None) if query else Q('institution_id', 'ne', None)
        query = query & Q('is_deleted', 'ne', True) if not deleted else query
        nodes = Node.find(query, allow_institution=True, **kwargs)
        return InstitutionQuerySet(nodes)

    @classmethod
    def find_one(cls, query=None, deleted=False, **kwargs):
        from website.models import Node
        if query and getattr(query, 'nodes', False):
            for node in query.nodes:
                if node._Q__key in cls.attribute_map:
                    node._Q__key = cls.attribute_map[node._Q__key]
        elif isinstance(query, RawQuery) and query._Q__key in cls.attribute_map:
            query._Q__key = cls.attribute_map[query._Q__key]
        query = query & Q('institution_id', 'ne', None) if query else Q('institution_id', 'ne', None)
        query = query & Q('is_deleted', 'ne', True) if not deleted else query
        node = Node.find_one(query, allow_institution=True, **kwargs)
        return cls(node)

    @classmethod
    def load(cls, key):
        from website.models import Node
        try:
            node = Node.find_one(Q('institution_id', 'eq', key), allow_institution=True)
            return cls(node)
        except NoResultsFound:
            return None

    def __repr__(self):
        return '<Institution ({}) with id \'{}\'>'.format(self.name, self._id)

    @property
    def pk(self):
        return self._id

    @property
    def api_v2_url(self):
        return reverse('institutions:institution-detail', kwargs={'institution_id': self._id, 'version': 'v2'})

    @property
    def absolute_api_v2_url(self):
        from api.base.utils import absolute_reverse
        return absolute_reverse('institutions:institution-detail', kwargs={'institution_id': self._id, 'version': 'v2'})

    @property
    def nodes_url(self):
        return self.absolute_api_v2_url + 'nodes/'

    @property
    def nodes_relationship_url(self):
        return self.absolute_api_v2_url + 'relationships/nodes/'

    @property
    def logo_path(self):
        if self.logo_name:
            return '/static/img/institutions/shields/{}'.format(self.logo_name)
        else:
            return None

    @property
    def logo_path_rounded_corners(self):
        logo_base = '/static/img/institutions/shields-rounded-corners/{}-rounded-corners.png'
        if self.logo_name:
            return logo_base.format(self.logo_name.replace('.png', ''))
        else:
            return None

    @property
    def banner_path(self):
        if self.banner_name:
            return '/static/img/institutions/banners/{}'.format(self.banner_name)
        else:
            return None
