"""
Firebird database backend for Django.

Requires firebirdsql: http://github.com/nakagami/pyfirebirdsql
"""
import re
import datetime
import binascii

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends import utils
from django.db.utils import DatabaseError as WrappedDatabaseError
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import SafeText
from django.utils.version import get_version_tuple

try:
    import firebirdsql as Database
except ImportError as e:
    raise ImproperlyConfigured("Error loading firebirdsql module: %s" % e)


from .client import DatabaseClient                          # NOQA isort:skip
from .creation import DatabaseCreation                      # NOQA isort:skip
from .features import DatabaseFeatures                      # NOQA isort:skip
from .introspection import DatabaseIntrospection            # NOQA isort:skip
from .operations import DatabaseOperations                  # NOQA isort:skip
from .schema import DatabaseSchemaEditor                    # NOQA isort:skip


def quote_value(value):
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return "'%s'" % value
    elif isinstance(value, str):
        return "'%s'" % value.replace("\'", "\'\'")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        return "x'%s'" % binascii.hexlify(value).decode('ascii')
    elif value is None:
        return "NULL"
    else:
        return str(value)


def convert_sql(query, params):
    if not params:
        return query

    converted_params = []
    for p in params:
        v = p
        if isinstance(v, datetime.datetime) and timezone.is_aware(v):
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        converted_params.append(quote_value(v))
    if len(converted_params) == 1:
        query = query % converted_params[0]
    else:
        query = query % tuple(converted_params)
    return query


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = 'firebirdsql'
    display_name = 'FirebirdSQL'
    # This dictionary maps Field objects to their associated FirebirdSQL column
    # types, as strings. Column-type strings can contain format strings; they'll
    # be interpolated against the values of Field.__dict__ before being output.
    # If a column type is set to None, it won't be included in the output.
    #
    # Any format strings starting with "qn_" are quoted before being used in the
    # output (the "qn_" prefix is stripped before the lookup is performed.
    data_types = {
        'AutoField': 'integer generated by default as identity',
        'BigAutoField': 'bigint generated by default as identity',
        'BinaryField': 'blob sub_type 0',
        'BooleanField': 'boolean',
        'CharField': 'varchar(%(max_length)s)',
        'DateField': 'date',
        'DateTimeField': 'timestamp',
        'DecimalField': 'decimal(%(max_digits)s, %(decimal_places)s)',
        'DurationField': 'bigint',
        'FileField': 'varchar(%(max_length)s)',
        'FilePathField': 'varchar(%(max_length)s)',
        'FloatField': 'double precision',
        'IntegerField': 'integer',
        'BigIntegerField': 'bigint',
        'IPAddressField': 'char(15)',
        'GenericIPAddressField': 'char(39)',
        'NullBooleanField': 'boolean',
        'OneToOneField': 'integer',
        'PositiveIntegerField': 'integer',
        'PositiveSmallIntegerField': 'smallint',
        'SlugField': 'varchar(%(max_length)s)',
        'SmallIntegerField': 'smallint',
        'TextField': 'blob sub_type 1',
        'TimeField': 'time',
        'UUIDField': 'char(32)',
    }
    data_type_check_constraints = {
        'PositiveIntegerField': '%(qn_column)s >= 0',
        'PositiveSmallIntegerField': '%(qn_column)s >= 0',
    }
    operators = {
        'exact': '= %s',
        'iexact': '= UPPER(%s)',
        'contains': "LIKE %s ESCAPE'\\'",
        'icontains': "LIKE UPPER(%s) ESCAPE'\\'",
        'regex': 'SIMILAR TO %s',
        'iregex': 'SIMILAR TO %s',
        'gt': '> %s',
        'gte': '>= %s',
        'lt': '< %s',
        'lte': '<= %s',
        'startswith': "LIKE %s ESCAPE'\\'",
        'endswith': "LIKE %s ESCAPE'\\'",
        'istartswith': "LIKE UPPER(%s) ESCAPE'\\'",
        'iendswith': "LIKE UPPER(%s) ESCAPE'\\'",
    }

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, *, _) should be
    # escaped on database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%%', '\%%'), '_', '\_')"
    pattern_ops = {
        'contains': "LIKE '%%' || {} || '%%'",
        'icontains': "LIKE '%%' || UPPER({}) || '%%'",
        'startswith': "LIKE {} || '%%'",
        'istartswith': "LIKE UPPER({}) || '%%'",
        'endswith': "LIKE '%%' || {}",
        'iendswith': "LIKE '%%' || UPPER({})",
    }

    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor
    # Classes instantiated in __init__().
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_connection_params(self):
        settings_dict = self.settings_dict
        if not settings_dict['NAME']:
            raise ImproperlyConfigured(
                "settings.DATABASES is improperly configured. "
                "Please supply the NAME value.")
        conn_params = {'charset': 'UTF8'}
        conn_params['database'] = settings_dict['NAME']
        conn_params.update(settings_dict['OPTIONS'])
        if settings_dict['USER']:
            conn_params['user'] = settings_dict['USER']
        if settings_dict['PASSWORD']:
            conn_params['password'] = settings_dict['PASSWORD']
        if settings_dict['HOST']:
            conn_params['host'] = settings_dict['HOST']
        if settings_dict['PORT']:
            conn_params['port'] = settings_dict['PORT']
        return conn_params

    def get_new_connection(self, conn_params):
        connection = Database.connect(**conn_params)
        return connection

    def init_connection_state(self):
        self._set_autocommit(self.get_autocommit())

    def _set_autocommit(self, autocommit):
        with self.wrap_database_errors:
            self.connection.set_autocommit(autocommit)

    def create_cursor(self, name=None):
        return self.connection.cursor(factory=FirebirdCursorWrapper)

    def is_usable(self):
        return not self.connection.is_disconnect()

    def close_if_unusable_or_obsolete(self):
        if self.errors_occurred:
            self.close()


class FirebirdCursorWrapper(Database.Cursor):
    def execute(self, query, params=None):
        query = convert_sql(query, params)
        return Database.Cursor.execute(self, query)

    def executemany(self, query, param_list):
        for params in param_list:
            Database.Cursor.execute(self, convert_sql(query, params))
