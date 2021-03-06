"""Unit test utilities.
"""

__author__ = ['Ryan Barrett <webutil@ryanb.org>']

import base64
import datetime
import difflib
import mox
import pprint
import re
import os
import rfc822
import StringIO
import traceback
import urllib2
import urlparse

from appengine_config import HTTP_TIMEOUT
import webapp2

from google.appengine.datastore import datastore_stub_util
from google.appengine.ext import db
from google.appengine.ext import ndb
from google.appengine.ext import testbed


def get_task_params(task):
  """Parses a task's POST body and returns the query params in a dict.
  """
  params = urlparse.parse_qs(base64.b64decode(task['body']))
  params = dict((key, val[0]) for key, val in params.items())
  return params


def get_task_eta(task):
  """Returns a task's ETA as a datetime."""
  return datetime.datetime.fromtimestamp(
    float(dict(task['headers'])['X-AppEngine-TaskETA']))


class HandlerTest(mox.MoxTestBase):
  """Base test class for webapp2 request handlers.

  Uses App Engine's testbed to set up API stubs:
  http://code.google.com/appengine/docs/python/tools/localunittesting.html

  Attributes:
    application: WSGIApplication
    handler: webapp2.RequestHandler
  """

  class UrlopenResult(object):
    """A fake urllib2.urlopen() result object. Also works for urlfetch.fetch().
    """
    def __init__(self, status_code, content, headers={}):
      self.status_code = status_code
      self.content = content
      self.headers = headers

    def read(self):
      return self.content

    def getcode(self):
      return self.status_code

    def info(self):
      return rfc822.Message(StringIO.StringIO(
          '\n'.join('%s: %s' % item for item in self.headers.items())))


  def setUp(self):
    super(HandlerTest, self).setUp()

    os.environ['APPLICATION_ID'] = 'app_id'
    self.current_user_id = '123'
    self.current_user_email = 'foo@bar.com'

    self.testbed = testbed.Testbed()
    self.testbed.setup_env(user_id=self.current_user_id,
                           user_email=self.current_user_email)
    self.testbed.activate()

    hrd_policy = datastore_stub_util.PseudoRandomHRConsistencyPolicy(probability=.5)
    self.testbed.init_datastore_v3_stub(consistency_policy=hrd_policy)
    self.testbed.init_taskqueue_stub(root_path='.')
    self.testbed.init_user_stub()
    self.testbed.init_mail_stub()
    self.testbed.init_memcache_stub()
    self.testbed.init_logservice_stub()

    self.mox.StubOutWithMock(urllib2, 'urlopen')

    # unofficial API, whee! this is so we can call
    # TaskQueueServiceStub.GetTasks() in tests. see
    # google/appengine/api/taskqueue/taskqueue_stub.py
    self.taskqueue_stub = self.testbed.get_stub('taskqueue')

    self.request = webapp2.Request.blank('/')
    self.response = webapp2.Response()
    self.handler = webapp2.RequestHandler(self.request, self.response)

    # set time zone to UTC so that tests don't depend on local time zone
    os.environ['TZ'] = 'UTC'

  def tearDown(self):
    self.testbed.deactivate()
    super(HandlerTest, self).tearDown()

  def expect_urlopen(self, url, response=None, status=200, data=None,
                     headers=None, response_headers={}, **kwargs):
    """Stubs out urllib2.urlopen() and sets up an expected call.

    If status isn't 2xx, makes the expected call raise a urllib2.HTTPError
    instead of returning the response.

    If data is set, url *must* be a urllib2.Request.

    If response is unset, returns the expected call.

    Args:
      url: string, re.RegexObject or urllib2.Request or webob.Request
      response: string
      status: int, HTTP response code
      data: optional string POST body
      headers: optional expected request header dict
      response_headers: optional response header dict
      kwargs: other keyword args, e.g. timeout
    """
    def check_request(req):
      try:
        expected = url if isinstance(url, re._pattern_type) else re.escape(url)
        if isinstance(req, basestring):
          self.assertRegexpMatches(req, expected)
          assert not data, data
          assert not headers, headers
        else:
          self.assertRegexpMatches(req.get_full_url(), expected)
          self.assertEqual(data, req.get_data())
          if isinstance(headers, mox.Comparator):
            self.assertTrue(headers.equals(req.header_items()))
          elif headers is not None:
            missing = set(headers.items()) - set(req.header_items())
            assert not missing, 'Missing request headers: %s' % missing
      except AssertionError:
        traceback.print_exc()
        return False
      return True

    call = urllib2.urlopen(mox.Func(check_request), timeout=HTTP_TIMEOUT, **kwargs)
    if status / 100 != 2:
      if response:
        response = StringIO.StringIO(response)
      call.AndRaise(urllib2.HTTPError('url', status, 'message',
                                      response_headers, response))
    elif response is not None:
      call.AndReturn(self.UrlopenResult(status, response, headers=response_headers))
    else:
      return call

  def assert_entities_equal(self, a, b, ignore=frozenset(), keys_only=False,
                            in_order=False):
    """Asserts that a and b are equivalent entities or lists of entities.

    ...specifically, that they have the same property values, and if they both
    have populated keys, that their keys are equal too.

    Args:
      a, b: db.Model or ndb.Model instances or lists of instances
      ignore: sequence of strings, property names not to compare
      keys_only: boolean, if True only compare keys
      in_order: boolean. If False, all entities must have keys.
    """
    if not isinstance(a, (list, tuple, db.Query, ndb.Query)):
      a = [a]
    if not isinstance(b, (list, tuple, db.Query, ndb.Query)):
      b = [b]

    key_fn = lambda e: e.key if isinstance(e, ndb.Model) else e.key()
    if not in_order:
      a = list(sorted(a, key=key_fn))
      b = list(sorted(b, key=key_fn))

    self.assertEqual(len(a), len(b),
                     'Different lengths:\n expected %s\n actual %s' % (a, b))

    flat_key = lambda e: e.key.flat() if isinstance(e, ndb.Model) else e.key().to_path()
    for x, y in zip(a, b):
      try:
        self.assertEqual(flat_key(x), flat_key(y))
      except (db.BadKeyError, db.NotSavedError):
        if keys_only:
          raise

      def props(e):
        all = e.to_dict() if isinstance(e, ndb.Model) else e.properties()
        return {k: v for k, v in all.items() if k not in ignore}

      if not keys_only:
        self.assert_equals(props(x), props(y))

  def entity_keys(self, entities):
    """Returns a list of keys for a list of entities.
    """
    return [e.key() for e in entities]

  def assert_equals(self, expected, actual, msg=None, in_order=False):
    """Pinpoints individual element differences in lists and dicts.

    If in_order is False, ignores order in lists and tuples.
    """
    try:
      self._assert_equals(expected, actual, in_order=in_order)
    except AssertionError, e:
      if not isinstance(expected, basestring):
        expected = pprint.pformat(expected)
      if not isinstance(actual, basestring):
        actual = pprint.pformat(actual)
      raise AssertionError("""\
%s: %s
Expected value:
%s
Actual value:
%s""" % (msg, ''.join(e.args), expected, actual))

  def _assert_equals(self, expected, actual, in_order=False):
    """Recursive helper for assert_equals().
    """
    key = None

    try:
      if isinstance(expected, re._pattern_type):
        if not re.match(expected, actual):
          self.fail("%r doesn't match %s" % (expected, actual))
      elif isinstance(expected, dict) and isinstance(actual, dict):
        for key in set(expected.keys()) | set(actual.keys()):
          self._assert_equals(expected.get(key), actual.get(key), in_order=in_order)
      elif isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if not in_order:
          expected = sorted(list(expected))
          actual = sorted(list(actual))
        self.assertEqual(len(expected), len(actual),
                         'Different lengths:\n expected %s\n actual %s' %
                         (len(expected), len(actual)))
        for key, (e, a) in enumerate(zip(expected, actual)):
          self._assert_equals(e, a, in_order=in_order)
      elif (isinstance(expected, basestring) and isinstance(actual, basestring) and
            '\n' in expected):
        self.assert_multiline_equals(expected, actual)
      else:
        self.assertEquals(expected, actual)

    except AssertionError, e:
      # fill in where this failure came from. this recursively builds,
      # backwards, all the way up to the root.
      args = ('[%s] ' % key if key is not None else '') + ''.join(e.args)
      raise AssertionError(args)

  def assert_multiline_equals(self, expected, actual):
    """Compares two multi-line strings and reports a diff style output.

    Ignores leading and trailing whitespace on each line, and squeezes repeated
    blank lines down to just one.
    """
    def normalize(val):
      lines = [l.strip() + '\n' for l in val.splitlines(True)]
      return [l for i, l in enumerate(lines)
              if i <= 1 or not (lines[i - 1] == l == '\n')]

    exp_lines = normalize(expected)
    act_lines = normalize(actual)
    if exp_lines != act_lines:
      self.fail(''.join(difflib.Differ().compare(exp_lines, act_lines)))
