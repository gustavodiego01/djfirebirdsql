import datetime

from django.db.models.fields import AutoField
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.backends.base.schema import _related_non_m2m_objects
from django.utils.encoding import force_text


def quote_value(value):
    import binascii
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


class DatabaseSchemaEditor(BaseDatabaseSchemaEditor):
    sql_rename_table = "Rename table is not allowed"  # Not supported
    sql_delete_table = "DROP TABLE %(table)s"
    sql_create_column = "ALTER TABLE %(table)s ADD %(column)s %(definition)s"
    sql_alter_column_type = "ALTER %(column)s TYPE %(type)s"
    sql_alter_column_default = "ALTER COLUMN %(column)s SET DEFAULT %(default)s"
    sql_alter_column_no_default = "ALTER COLUMN %(column)s DROP DEFAULT"
    sql_delete_column = "ALTER TABLE %(table)s DROP %(column)s"
    sql_rename_column = "ALTER TABLE %(table)s ALTER %(old_column)s TO %(new_column)s"
    sql_create_fk = "ALTER TABLE %(table)s ADD CONSTRAINT %(name)s FOREIGN KEY (%(column)s) REFERENCES %(to_table)s (%(to_column)s) ON DELETE CASCADE"
    sql_delete_fk = "ALTER TABLE %(table)s DROP CONSTRAINT %(name)s"

    def prepare_default(self, value):
        return quote_value(value)

    def _get_field_indexes(self, model, field):
        with self.connection.cursor() as cursor:
            indexes = self.connection.introspection._get_field_indexes(cursor, model._meta.db_table, field.column)
        return indexes

    def alter_field(self, model, old_field, new_field, strict=False):
        old_db_params = old_field.db_parameters(connection=self.connection)
        old_type = old_db_params['type']
        new_db_params = new_field.db_parameters(connection=self.connection)
        new_type = new_db_params['type']
        if old_type != new_type:
            for index_name, constraint_type, constraint_name in self._get_field_indexes(model, old_field):
                # TODO
                #self.execute("DROP INDEX %s" % name)
                pass
        super().alter_field(model, old_field, new_field, strict)

    def delete_model(self, model):
        """Delete a model from the database."""
        # delete related foreign key constraints
        with self.connection.cursor() as cursor:
            references = self.connection.introspection._get_references(cursor, model._meta.db_table)
            for r in references:
                self.execute(self.sql_delete_fk % {'name': r[0], 'table': r[1].upper()})
        super().delete_model(model)

    def _column_has_default(self, params):
        sql = """
            SELECT a.RDB$DEFAULT_VALUE
            FROM RDB$RELATION_FIELDS a
            WHERE UPPER(a.RDB$FIELD_NAME) = UPPER('%(column)s')
            AND UPPER(a.RDB$RELATION_NAME) = UPPER('%(table_name)s')
        """
        value = self.execute(sql % params)
        return True if value else False

    def _column_sql(self, model, field):
        """
        Take a field and return its column definition.
        The field must already have had set_attributes_from_name() called.
        """
        # Get the column's type and use that as the basis of the SQL
        db_params = field.db_parameters(connection=self.connection)
        sql = db_params['type']

        # Primary key/unique outputs
        if field.primary_key:
            sql += " PRIMARY KEY"
        elif field.unique:
            sql += " UNIQUE"

        # Return the sql
        return sql
