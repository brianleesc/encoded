from pkg_resources import parse_version
from pyramid.interfaces import (
    PHASE1_CONFIG,
    PHASE2_CONFIG,
)
import venusian


def includeme(config):
    config.registry['migrator'] = Migrator()
    config.add_directive('add_upgrade', add_upgrade)
    config.add_directive('add_upgrade_step', add_upgrade_step)
    config.add_directive('set_upgrade_finalizer', set_upgrade_finalizer)
    config.add_directive(
        'set_default_upgrade_finalizer', set_default_upgrade_finalizer)
    config.add_request_method(upgrade, 'upgrade')


class ConfigurationError(Exception):
    pass


class UpgradeError(Exception):
    pass


class UpgradePathNotFound(UpgradeError):
    def __str__(self):
        return "%r from %r to %r (at %r)" % self.args


class VersionTooHigh(UpgradeError):
    pass


class Migrator(object):
    """ Migration manager
    """
    def __init__(self):
        self.schema_migrators = {}
        self.default_finalizer = None

    def add_upgrade(self, schema_name, version, finalizer=None):
        if schema_name in self.schema_migrators:
            raise ConfigurationError('duplicate schema_name', schema_name)
        if finalizer is None:
            finalizer = self.default_finalizer
        schema_migrator = SchemaMigrator(schema_name, version, finalizer)
        self.schema_migrators[schema_name] = schema_migrator

    def upgrade(self, schema_name, value,
                current_version='', target_version=None, **kw):
        schema_migrator = self.schema_migrators[schema_name]
        return schema_migrator.upgrade(
            value, current_version, target_version, **kw)

    def __getitem__(self, schema_name):
        return self.schema_migrators[schema_name]

    def __contains__(self, schema_name):
        return schema_name in self.schema_migrators


class SchemaMigrator(object):
    """ Manages upgrade steps
    """
    def __init__(self, name, version, finalizer=None):
        self.__name__ = name
        self.version = version
        self.upgrade_steps = {}
        self.finalizer = finalizer

    def add_upgrade_step(self, step, source='', dest=None):
        if dest is None:
            dest = self.version
        if parse_version(dest) <= parse_version(source):
            raise ValueError("dest is less than source", dest, source)
        if parse_version(source) in self.upgrade_steps:
            raise ConfigurationError('duplicate step for source', source)
        self.upgrade_steps[parse_version(source)] = UpgradeStep(step, source, dest)

    def upgrade(self, value, current_version='', target_version=None, finalize=True, **kw):
        if target_version is None:
            target_version = self.version

        if parse_version(current_version) > parse_version(target_version):
            raise VersionTooHigh(self.__name__, current_version, target_version)

        # Try to find a path from current to target versions
        steps = []
        version = current_version

        # If no entry exists for the current_version, fallback to ''
        if parse_version(version) not in self.upgrade_steps:
            try:
                step = self.upgrade_steps[parse_version('')]
            except KeyError:
                pass
            else:
                if parse_version(step.dest) >= parse_version(version):
                    steps.append(step)
                    version = step.dest

        while parse_version(version) < parse_version(target_version):
            try:
                step = self.upgrade_steps[parse_version(version)]
            except KeyError:
                break
            steps.append(step)
            version = step.dest

        if version != target_version:
            raise UpgradePathNotFound(
                self.__name__, current_version, target_version, version)

        # Apply the steps

        system = {}
        system.update(kw)

        for step in steps:
            next_value = step(value, system)
            if next_value is not None:
                value = next_value

        if finalize and self.finalizer is not None:
            next_value = self.finalizer(value, system, version)
            if next_value is not None:
                value = next_value

        return value


class UpgradeStep(object):
    def __init__(self, step, source, dest):
        self.step = step
        self.source = source
        self.dest = dest

    def __call__(self, value, system):
        return self.step(value, system)


# Imperative configuration

def add_upgrade(config, schema_name, version, finalizer=None):
    if finalizer is not None:
        config.set_upgrade_finalizer(schema_name, finalizer)

    def callback():
        migrator = config.registry['migrator']
        migrator.add_upgrade(schema_name, version)

    config.action(
        ('add_upgrade', schema_name),
        callback, order=PHASE2_CONFIG)


def add_upgrade_step(config, schema_name, step, source='', dest=None):

    def callback():
        migrator = config.registry['migrator']
        migrator[schema_name].add_upgrade_step(step, source, dest)

    config.action(
        ('add_upgrade_step', schema_name, parse_version(source)),
        callback)


def set_upgrade_finalizer(config, schema_name, finalizer):

    def callback():
        migrator = config.registry['migrator']
        migrator[schema_name].finalizer = finalizer

    config.action(
        ('set_upgrade_finalizer', schema_name),
        callback)


def set_default_upgrade_finalizer(config, finalizer):

    def callback():
        migrator = config.registry['migrator']
        migrator.default_finalizer = finalizer

    config.action(
        'set_default_upgrade_finalizer',
        callback, order=PHASE1_CONFIG)


# Declarative configuration

def upgrade_step(schema_name, source='', dest=None):
    """ Register an upgrade step
    """

    def decorate(step):
        def callback(scanner, factory_name, factory):
            scanner.config.add_upgrade_step(schema_name, step, source, dest)

        venusian.attach(step, callback, category='migrator')
        return step

    return decorate


def upgrade_finalizer(schema_name):
    """ Register a finalizer
    """

    def decorate(finalizer):
        def callback(scanner, factory_name, factory):
            scanner.config.set_upgrade_finalizer(schema_name, finalizer)

        venusian.attach(finalizer, callback, category='migrator')
        return finalizer

    return decorate


def default_upgrade_finalizer(finalizer):
    def callback(scanner, factory_name, factory):
        scanner.config.set_default_upgrade_finalizer(finalizer)

    venusian.attach(finalizer, callback, category='migrator')
    return finalizer


# Upgrade
def upgrade(request, schema_name, value,
            current_version='', target_version=None, **kw):
    migrator = request.registry['migrator']
    return migrator.upgrade(
        schema_name, value, current_version='', target_version=None,
        request=request, **kw)
