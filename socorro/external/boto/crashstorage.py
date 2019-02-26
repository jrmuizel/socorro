# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/

import json

from configman import Namespace
from configman.converters import class_converter
import json_schema_reducer
from future.utils import iteritems

from socorro.external.crashstorage_base import (
    CrashStorageBase,
    CrashIDNotFound,
    MemoryDumpsMapping,
)
from socorro.external.es.super_search_fields import SuperSearchFields
from socorro.lib.transaction import retry
from socorro.schemas import CRASH_REPORT_JSON_SCHEMA


class BotoCrashStorage(CrashStorageBase):
    """Saves and loads crash data to S3"""
    required_config = Namespace()
    required_config.add_option(
        'resource_class',
        default='socorro.external.boto.connection_context.ConnectionContextBase',
        doc='fully qualified dotted Python classname to handle Boto connections',
        from_string_converter=class_converter,
        reference_value_from='resource.boto'
    )
    required_config.add_option(
        'temporary_file_system_storage_path',
        doc='a local filesystem path where dumps temporarily during processing',
        default='/home/socorro/temp',
        reference_value_from='resource.boto',
    )
    required_config.add_option(
        'dump_file_suffix',
        doc='the suffix used to identify a dump file (for use in temp files)',
        default='.dump',
        reference_value_from='resource.boto',
    )
    required_config.add_option(
        'json_object_hook',
        default='configman.dotdict.DotDict',
        from_string_converter=class_converter,
    )

    def __init__(self, config, namespace='', quit_check_callback=None):
        super().__init__(config, namespace=namespace, quit_check_callback=quit_check_callback)
        self.connection_source = config.resource_class(config)

    @staticmethod
    def do_save_raw_crash(boto_connection, raw_crash, dumps, crash_id):
        if dumps is None:
            dumps = MemoryDumpsMapping()

        raw_crash_data = boto_connection._convert_mapping_to_string(raw_crash).encode('utf-8')
        boto_connection.submit(crash_id, 'raw_crash', raw_crash_data)

        dump_names_data = boto_connection._convert_list_to_string(dumps.keys()).encode('utf-8')
        boto_connection.submit(crash_id, 'dump_names', dump_names_data)

        # We don't know what type of dumps mapping we have. We do know,
        # however, that by calling the memory_dump_mapping method, we will get
        # a MemoryDumpMapping which is exactly what we need.
        dumps = dumps.as_memory_dumps_mapping()
        for dump_name, dump in iteritems(dumps):
            if dump_name in (None, '', 'upload_file_minidump'):
                dump_name = 'dump'
            boto_connection.submit(crash_id, dump_name, dump)

    def save_raw_crash(self, raw_crash, dumps, crash_id):
        retry(
            self.connection_source,
            self.quit_check,
            self.do_save_raw_crash,
            raw_crash=raw_crash,
            dumps=dumps,
            crash_id=crash_id
        )

    @staticmethod
    def _do_save_processed(boto_connection, processed_crash):
        crash_id = processed_crash['uuid']
        data = boto_connection._convert_mapping_to_string(processed_crash).encode('utf-8')
        boto_connection.submit(crash_id, "processed_crash", data)

    def save_processed(self, processed_crash):
        retry(
            self.connection_source,
            self.quit_check,
            self._do_save_processed,
            processed_crash=processed_crash
        )

    def save_raw_and_processed(self, raw_crash, dumps, processed_crash, crash_id):
        # NOTE(willkg): Don't save the raw_crash again because that messes up
        # the original data we got. bug 866973
        self.save_processed(processed_crash)

    @staticmethod
    def do_get_raw_crash(boto_connection, crash_id, json_object_hook):
        try:
            raw_crash_as_string = boto_connection.fetch(crash_id, 'raw_crash')
            return json.loads(raw_crash_as_string, object_hook=json_object_hook)
        except boto_connection.ResponseError as x:
            raise CrashIDNotFound('%s not found: %s' % (crash_id, x))

    def get_raw_crash(self, crash_id):
        return retry(
            self.connection_source,
            self.quit_check,
            self.do_get_raw_crash,
            crash_id=crash_id,
            json_object_hook=self.config.json_object_hook
        )

    @staticmethod
    def do_get_raw_dump(boto_connection, crash_id, name=None):
        try:
            if name in (None, '', 'upload_file_minidump'):
                name = 'dump'
            a_dump = boto_connection.fetch(crash_id, name)
            return a_dump
        except boto_connection.ResponseError as x:
            raise CrashIDNotFound('%s not found: %s' % (crash_id, x))

    def get_raw_dump(self, crash_id, name=None):
        return retry(
            self.connection_source,
            self.quit_check,
            self.do_get_raw_dump,
            crash_id=crash_id,
            name=name
        )

    @staticmethod
    def do_get_raw_dumps(boto_connection, crash_id):
        try:
            dump_names_as_string = boto_connection.fetch(crash_id, 'dump_names')
            dump_names = boto_connection._convert_string_to_list(dump_names_as_string)

            dumps = MemoryDumpsMapping()
            for dump_name in dump_names:
                if dump_name in (None, '', 'upload_file_minidump'):
                    dump_name = 'dump'
                dumps[dump_name] = boto_connection.fetch(crash_id, dump_name)
            return dumps
        except boto_connection.ResponseError as x:
            raise CrashIDNotFound('%s not found: %s' % (crash_id, x))

    def get_raw_dumps(self, crash_id):
        """Fetch raw dumps

        :returns: MemoryDumpsMapping

        """
        return retry(
            self.connection_source,
            self.quit_check,
            self.do_get_raw_dumps,
            crash_id=crash_id
        )

    def get_raw_dumps_as_files(self, crash_id):
        in_memory_dumps = self.get_raw_dumps(crash_id)
        # convert our native memory dump mapping into a file dump mapping.
        return in_memory_dumps.as_file_dumps_mapping(
            crash_id,
            self.config.temporary_file_system_storage_path,
            self.config.dump_file_suffix
        )

    @staticmethod
    def _do_get_unredacted_processed(boto_connection, crash_id, json_object_hook):
        try:
            processed_crash_as_string = boto_connection.fetch(crash_id, 'processed_crash')
            return json.loads(processed_crash_as_string, object_hook=json_object_hook,)
        except boto_connection.ResponseError as x:
            raise CrashIDNotFound('%s not found: %s' % (crash_id, x))

    def get_unredacted_processed(self, crash_id):
        return retry(
            self.connection_source,
            self.quit_check,
            self._do_get_unredacted_processed,
            crash_id=crash_id,
            json_object_hook=self.config.json_object_hook
        )


class BotoS3CrashStorage(BotoCrashStorage):
    required_config = Namespace()
    required_config.add_option(
        'resource_class',
        default='socorro.external.boto.connection_context.RegionalS3ConnectionContext',
        doc='fully qualified dotted Python classname to handle Boto connections',
        from_string_converter=class_converter,
        reference_value_from='resource.boto'
    )


class TelemetryBotoS3CrashStorage(BotoS3CrashStorage):
    """Sends a subset of the processed crash to an S3 bucket

    The subset of the processed crash is based on the JSON Schema which is
    derived from "socorro/external/es/super_search_fields.py".

    """

    required_config = Namespace()
    required_config.add_option(
        'resource_class',
        default='socorro.external.boto.connection_context.RegionalS3ConnectionContext',
        doc='fully qualified dotted Python classname to handle Boto connections',
        from_string_converter=class_converter,
        reference_value_from='resource.boto'
    )
    required_config.elasticsearch = Namespace()
    required_config.elasticsearch.add_option(
        'elasticsearch_class',
        default='socorro.external.es.connection_context.ConnectionContext',
        from_string_converter=class_converter,
        reference_value_from='resource.elasticsearch',
    )

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self._all_fields = SuperSearchFields(config=self.config).get()

    def save_raw_and_processed(self, raw_crash, dumps, processed_crash, crash_id):
        crash_report = {}

        # TODO Opportunity of optimization: We could inspect
        # CRASH_REPORT_JSON_SCHEMA and get a list of all (recursive) keys that
        # are in there and use that to limit the two following loops to not
        # bother filling up `crash_report` with keys that will never be needed.

        # Rename fields in raw_crash
        raw_fields_map = dict(
            (x['in_database_name'], x['name'])
            for x in self._all_fields.values()
            if x['namespace'] == 'raw_crash'
        )
        for key, val in raw_crash.items():
            crash_report[raw_fields_map.get(key, key)] = val

        # Rename fields in processed_crash
        processed_fields_map = dict(
            (x['in_database_name'], x['name'])
            for x in self._all_fields.values()
            if x['namespace'] == 'processed_crash'
        )
        for key, val in processed_crash.items():
            crash_report[processed_fields_map.get(key, key)] = val

        # Validate crash_report
        crash_report = json_schema_reducer.make_reduced_dict(CRASH_REPORT_JSON_SCHEMA, crash_report)
        self.save_processed(crash_report)

    @staticmethod
    def _do_save_processed(boto_connection, processed_crash):
        """Overriding this to change "name of thing" to crash_report"""
        crash_id = processed_crash['uuid']
        data = boto_connection._convert_mapping_to_string(processed_crash).encode('utf-8')
        boto_connection.submit(crash_id, "crash_report", data)

    @staticmethod
    def _do_get_unredacted_processed(boto_connection, crash_id, json_object_hook):
        """Overriding this to change "name of thing" to crash_report"""
        try:
            processed_crash_as_string = boto_connection.fetch(crash_id, 'crash_report')
            return json.loads(processed_crash_as_string, object_hook=json_object_hook)
        except boto_connection.ResponseError as x:
            raise CrashIDNotFound('%s not found: %s' % (crash_id, x))
