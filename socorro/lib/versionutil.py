# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


class VersionParseError(Exception):
    """Raised if the version isn't parseable"""
    pass


def validate_version(version):
    """Validate a version."""
    if not (version and isinstance(version, str)):
        return False

    version = version.split('.')

    # Versions should have at least two parts: X.Y
    if len(version) < 2:
        return False

    # First part should consist of one or more ascii digits
    if not (len(version[0]) > 0 and version[0].isdigit()):
        return False

    return True


def generate_version_key(version):
    """Serialize version into a string that can sort with other versions.

    :arg str version: the version string; e.g. "62.0.3b15rc1"

    :returns: a sortable version of the version string to be used as a key
        for sorting

    Example:

    >>> generate_version_key('62.0.2b5rc1')
    '062000002b005001'

    """
    if not validate_version(version):
        raise VersionParseError('Version %s does not validate' % version)

    orig_version = version
    try:
        if 'rc' in version:
            version, rc = version.split('rc')
        else:
            # We use 999 so that the release always sorts after the release
            # candidates
            rc = 999

        if 'pre' in version:
            # Treat "pre" like we do rc, but call it 1 if there's no number
            version, rc = version.split('pre')
            if not rc:
                rc = 1

        ending = []
        if 'a' in version:
            version, num = version.split('a')
            ending = ['a', 1, int(rc)]
        elif 'b' in version:
            version, num = version.split('b')
            # Handle the 62.0b case which is a superset of betas
            if not num:
                num = 999
            ending = ['b', int(num), int(rc)]
        elif 'esr' in version:
            version = version.replace('esr', '')
            ending = ['x', 0, int(rc)]
        else:
            ending = ['r', 0, int(rc)]

        version = [int(part) for part in version.split('.')]

        while len(version) < 3:
            version.append(0)

        version.extend(ending)

        # (x, y, z, channel, beta number, rc number)
        return '%03d%03d%03d%s%03d%03d' % tuple(version)

    except (ValueError, IndexError, TypeError) as exc:
        raise VersionParseError(
            'Version %s does not parse: %s' % (repr(orig_version), str(exc))
        )
