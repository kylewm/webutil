"""App Engine datastore model base classes and utilites.
"""

__author__ = ['Ryan Barrett <webutil@ryanb.org>']

import functools

from google.appengine.ext import db
from google.appengine.ext import ndb


class StringIdModel(ndb.Model):
  """An ndb model class that requires a string id."""

  def put(self, *args, **kwargs):
    """Raises AssertionError if string id is not provided."""
    assert self.key and self.key.string_id(), 'string id required but not provided'
    return super(StringIdModel, self).put(*args, **kwargs)


class KeyNameModel(db.Model):
  """A db model class that requires a key name."""

  def __init__(self, *args, **kwargs):
    """Raises AssertionError if key name is not provided."""
    super(KeyNameModel, self).__init__(*args, **kwargs)
    try:
      assert self.key().name()
    except db.NotSavedError:
      assert False, 'key name required but not provided'

  def __str__(self):
    return self.key().to_path()


class SingleEGModel(db.Model):
  """A model class that stores all entities in a single entity group.

  All entities use the same parent key (below), and all() automatically adds it
  as an ancestor. That allows, among other things, fetching all entities of
  this kind with strong consistency.
  """

  def enforce_parent(fn):
    """Sets the parent keyword arg. If it's already set, checks that it's correct."""
    @functools.wraps(fn)
    def wrapper(self_or_cls, *args, **kwargs):
      if '_from_entity' not in kwargs:
        parent = self_or_cls.shared_parent_key()
        if 'parent' in kwargs:
          assert kwargs['parent'] == parent, "Can't override parent in SingleEGModel"
        kwargs['parent'] = parent

      return fn(self_or_cls, *args, **kwargs)

    return wrapper

  @classmethod
  def shared_parent_key(cls):
    """Returns the shared parent key for this class.

    It's not actually an entity, just a placeholder key.
    """
    return db.Key.from_path('Parent', cls.kind())

  @enforce_parent
  def __init__(self, *args, **kwargs):
    super(SingleEGModel, self).__init__(*args, **kwargs)

  @classmethod
  @enforce_parent
  def get_by_id(cls, id, **kwargs):
    return super(SingleEGModel, cls).get_by_id(id, **kwargs)

  @classmethod
  @enforce_parent
  def get_by_key_name(cls, key_name, **kwargs):
    return super(SingleEGModel, cls).get_by_key_name(key_name, **kwargs)

  @classmethod
  @enforce_parent
  def get_or_insert(cls, key_name, **kwargs):
    return super(SingleEGModel, cls).get_or_insert(key_name, **kwargs)

  @classmethod
  def all(cls):
    return db.Query(cls).ancestor(cls.shared_parent_key())
