from __future__ import print_function

"""Command-line client for managing admin-only interactions with the aurora scheduler.
"""

import os
import optparse
import subprocess

from twitter.aurora.admin.mesos_maintenance import MesosMaintenance
from twitter.aurora.client.api import AuroraClientAPI
from twitter.aurora.client.base import check_and_log_response, die, requires
from twitter.aurora.common.clusters import CLUSTERS
from twitter.common import app, log
from twitter.common.quantity import Amount, Data
from twitter.common.quantity.parse_simple import parse_data

from gen.twitter.aurora.constants import ACTIVE_STATES, TERMINAL_STATES
from gen.twitter.aurora.ttypes import (
    ResponseCode,
    ScheduleStatus,
    TaskQuery,
)


GROUPING_OPTION = optparse.Option(
    '--grouping',
    type='choice',
    choices=MesosMaintenance.GROUPING_FUNCTIONS.keys(),
    metavar='GROUPING',
    default=MesosMaintenance.DEFAULT_GROUPING,
    dest='grouping',
    help='Grouping function to use to group hosts.  Options: %s.  Default: %%default' % (
        ', '.join(MesosMaintenance.GROUPING_FUNCTIONS.keys())))


def parse_hosts(options):
  if not (options.filename or options.hosts):
    die('Please specify either --filename or --hosts')
  if options.filename:
    with open(options.filename, 'r') as hosts:
      hosts = [hostname.strip() for hostname in hosts]
  elif options.hosts:
    hosts = [hostname.strip() for hostname in options.hosts.split(",")]
  if not hosts:
    die('No valid hosts found.')
  return hosts


@app.command
@app.command_option('--force', dest='force', default=False, action='store_true',
    help='Force expensive queries to run.')
@app.command_option('--shards', dest='shards', default=None,
    help='Only match given shards of a job.')
@app.command_option('--states', dest='states', default='RUNNING',
    help='Only match tasks with given state(s).')
@app.command_option('-l', '--listformat', dest='listformat',
    default="%role%/%jobName%/%instanceId% %status%",
    help='Format string of job/task items to print out.')
# TODO(ksweeney): Allow query by environment here.
def query(args, options):
  """usage: query [--shards=N[,N,...]]
                  [--states=State[,State,...]]
                  cluster [role [job]]

  Query Mesos about jobs and tasks.
  """
  def _convert_fmt_string(fmtstr):
    import re
    def convert(match):
      return "%%(%s)s" % match.group(1)
    return re.sub(r'%(\w+)%', convert, fmtstr)

  def flatten_task(t, d={}):
    for key in t.__dict__.keys():
      val = getattr(t, key)
      try:
        val.__dict__.keys()
      except AttributeError:
        d[key] = val
      else:
        flatten_task(val, d)

    return d

  def map_values(d):
    default_value = lambda v: v
    mapping = {
      'status': lambda v: ScheduleStatus._VALUES_TO_NAMES[v],
    }
    return dict(
      (k, mapping.get(k, default_value)(v)) for (k, v) in d.items()
    )

  for state in options.states.split(','):
    if state not in ScheduleStatus._NAMES_TO_VALUES:
      msg = "Unknown state '%s' specified.  Valid states are:\n" % state
      msg += ','.join(ScheduleStatus._NAMES_TO_VALUES.keys())
      die(msg)

  # Role, Job, Instances, States, and the listformat
  if len(args) == 0:
    die('Must specify at least cluster.')

  cluster = args[0]
  role = args[1] if len(args) > 1 else None
  job = args[2] if len(args) > 2 else None
  instances = set(map(int, options.shards.split(','))) if options.shards else set()

  if options.states:
    states = set(map(ScheduleStatus._NAMES_TO_VALUES.get, options.states.split(',')))
  else:
    states = ACTIVE_STATES | TERMINAL_STATES
  listformat = _convert_fmt_string(options.listformat)

  #  Figure out "expensive" queries here and bone if they do not have --force
  #  - Does not specify role
  if role is None and not options.force:
    die('--force is required for expensive queries (no role specified)')

  #  - Does not specify job
  if job is None and not options.force:
    die('--force is required for expensive queries (no job specified)')

  #  - Specifies status outside of ACTIVE_STATES
  if not (states <= ACTIVE_STATES) and not options.force:
    die('--force is required for expensive queries (states outside ACTIVE states')

  api = AuroraClientAPI(CLUSTERS[cluster], options.verbosity)
  query_info = api.query(api.build_query(role, job, instances=instances, statuses=states))
  tasks = query_info.result.scheduleStatusResult.tasks
  if query_info.responseCode != ResponseCode.OK:
    die('Failed to query scheduler: %s' % query_info.message)
  if tasks is None:
    return

  try:
    for task in tasks:
      d = flatten_task(task)
      print(listformat % map_values(d))
  except KeyError:
    msg = "Unknown key in format string.  Valid keys are:\n"
    msg += ','.join(d.keys())
    die(msg)


@app.command
@requires.exactly('cluster', 'role', 'cpu', 'ramMb', 'diskMb')
def set_quota(cluster, role, cpu_str, ram_mb_str, disk_mb_str):
  """usage: set_quota cluster role cpu ramMb diskMb

  Alters the amount of production quota allocated to a user.
  """
  try:
    cpu = float(cpu_str)
    ram_mb = int(ram_mb_str)
    disk_mb = int(disk_mb_str)
  except ValueError:
    log.error('Invalid value')

  options = app.get_options()
  resp = AuroraClientAPI(CLUSTERS[cluster], options.verbosity).set_quota(role, cpu, ram_mb, disk_mb)
  check_and_log_response(resp)


@app.command
@app.command_option('--filename', dest='filename', default=None,
    help='Name of the file with hostnames')
@app.command_option('--hosts', dest='hosts', default=None,
    help='Comma separated list of hosts')
@requires.exactly('cluster')
def start_maintenance_hosts(cluster):
  """usage: start_maintenance_hosts cluster [--filename=filename]
                                            [--hosts=hosts]
  """
  options = app.get_options()
  MesosMaintenance(CLUSTERS[cluster], options.verbosity).start_maintenance(parse_hosts(options))


@app.command
@app.command_option('--filename', dest='filename', default=None,
    help='Name of the file with hostnames')
@app.command_option('--hosts', dest='hosts', default=None,
    help='Comma separated list of hosts')
@requires.exactly('cluster')
def end_maintenance_hosts(cluster):
  """usage: end_maintenance_hosts cluster [--filename=filename]
                                          [--hosts=hosts]
  """
  options = app.get_options()
  MesosMaintenance(CLUSTERS[cluster], options.verbosity).end_maintenance(parse_hosts(options))


@app.command
@app.command_option('--filename', dest='filename', default=None,
    help='Name of the file with hostnames')
@app.command_option('--hosts', dest='hosts', default=None,
    help='Comma separated list of hosts')
@app.command_option('--batch_size', dest='batch_size', default=1,
    help='Number of groups to operate on at a time.')
@app.command_option('--post_drain_script', dest='post_drain_script', default=None,
    help='Path to a script to run for each host.')
@app.command_option(GROUPING_OPTION)
@requires.exactly('cluster')
def perform_maintenance_hosts(cluster):
  """usage: perform_maintenance cluster [--filename=filename]
                                        [--hosts=hosts]
                                        [--batch_size=num]
                                        [--post_drain_script=path]
                                        [--grouping=function]

  Asks the scheduler to remove any running tasks from the machine and remove it
  from service temporarily, perform some action on them, then return the machines
  to service.
  """
  options = app.get_options()
  drainable_hosts = parse_hosts(options)

  if options.post_drain_script:
    if not os.path.exists(options.post_drain_script):
      die("No such file: %s" % options.post_drain_script)
    cmd = os.path.abspath(options.post_drain_script)
    drained_callback = lambda host: subprocess.Popen([cmd, host])
  else:
    drained_callback = None

  MesosMaintenance(CLUSTERS[cluster], options.verbosity).perform_maintenance(
      drainable_hosts,
      batch_size=int(options.batch_size),
      callback=drained_callback,
      grouping_function=options.grouping)


@app.command
@app.command_option('--filename', dest='filename', default=None,
    help='Name of the file with hostnames')
@app.command_option('--hosts', dest='hosts', default=None,
    help='Comma separated list of hosts')
@requires.exactly('cluster')
def host_maintenance_status(cluster):
  """usage: host_maintenance_status cluster [--filename=filename]
                                            [--hosts=hosts]

  Check on the schedulers maintenance status for a list of hosts in the cluster.
  """
  options = app.get_options()
  checkable_hosts = parse_hosts(options)
  statuses = MesosMaintenance(CLUSTERS[cluster], options.verbosity).check_status(checkable_hosts)
  for pair in statuses:
    log.info("%s is in state: %s" % pair)


@app.command
@requires.exactly('cluster', 'role', 'cpu', 'ram', 'disk')
def increase_quota(cluster, role, cpu_str, ram_str, disk_str):
  """usage: increase_quota cluster role cpu ram[unit] disk[unit]

  Increases the amount of production quota allocated to a user.
  """
  cpu = float(cpu_str)
  ram = parse_data(ram_str)
  disk = parse_data(disk_str)

  options = app.get_options()
  client = AuroraClientAPI(CLUSTERS[cluster], options.verbosity == 'verbose')
  resp = client.get_quota(role)
  quota = resp.result.getQuotaResult.quota
  log.info('Current quota for %s:\n\tCPU\t%s\n\tRAM\t%s MB\n\tDisk\t%s MB' %
           (role, quota.numCpus, quota.ramMb, quota.diskMb))

  new_cpu = cpu + quota.numCpus
  new_ram = ram + Amount(quota.ramMb, Data.MB)
  new_disk = disk + Amount(quota.diskMb, Data.MB)

  log.info('Attempting to update quota for %s to\n\tCPU\t%s\n\tRAM\t%s MB\n\tDisk\t%s MB' %
           (role, new_cpu, new_ram.as_(Data.MB), new_disk.as_(Data.MB)))

  resp = client.set_quota(role, new_cpu, new_ram.as_(Data.MB), new_disk.as_(Data.MB))
  check_and_log_response(resp)


@app.command
@requires.exactly('cluster')
def scheduler_backup_now(cluster):
  """usage: scheduler_backup_now cluster

  Immediately initiates a full storage backup.
  """
  options = app.get_options()
  check_and_log_response(AuroraClientAPI(CLUSTERS[cluster], options.verbosity).perform_backup())


@app.command
@requires.exactly('cluster')
def scheduler_list_backups(cluster):
  """usage: scheduler_list_backups cluster

  Lists backups available for recovery.
  """
  options = app.get_options()
  resp = AuroraClientAPI(CLUSTERS[cluster], options.verbosity).list_backups()
  check_and_log_response(resp)
  backups = resp.result.listBackupsResult.backups
  print('%s available backups:' % len(backups))
  for backup in backups:
    print(backup)


@app.command
@requires.exactly('cluster', 'backup_id')
def scheduler_stage_recovery(cluster, backup_id):
  """usage: scheduler_stage_recovery cluster backup_id

  Stages a backup for recovery.
  """
  options = app.get_options()
  check_and_log_response(
      AuroraClientAPI(CLUSTERS[cluster], options.verbosity).stage_recovery(backup_id))


@app.command
@requires.exactly('cluster')
def scheduler_print_recovery_tasks(cluster):
  """usage: scheduler_print_recovery_tasks cluster

  Prints all active tasks in a staged recovery.
  """
  options = app.get_options()
  resp = AuroraClientAPI(CLUSTERS[cluster], options.verbosity).query_recovery(
      TaskQuery(statuses=ACTIVE_STATES))
  check_and_log_response(resp)
  log.info('Role\tJob\tShard\tStatus\tTask ID')
  for task in resp.tasks:
    assigned = task.assignedTask
    conf = assigned.task
    log.info('\t'.join((conf.owner.role,
                        conf.jobName,
                        str(assigned.instanceId),
                        ScheduleStatus._VALUES_TO_NAMES[task.status],
                        assigned.taskId)))


@app.command
@requires.exactly('cluster', 'task_ids')
def scheduler_delete_recovery_tasks(cluster, task_ids):
  """usage: scheduler_delete_recovery_tasks cluster task_ids

  Deletes a comma-separated list of task IDs from a staged recovery.
  """
  ids = set(task_ids.split(','))
  options = app.get_options()
  check_and_log_response(AuroraClientAPI(CLUSTERS[cluster], options.verbosity)
      .delete_recovery_tasks(TaskQuery(taskIds=ids)))


@app.command
@requires.exactly('cluster')
def scheduler_commit_recovery(cluster):
  """usage: scheduler_commit_recovery cluster

  Commits a staged recovery.
  """
  options = app.get_options()
  check_and_log_response(AuroraClientAPI(CLUSTERS[cluster], options.verbosity)
      .commit_recovery())


@app.command
@requires.exactly('cluster')
def scheduler_unload_recovery(cluster):
  """usage: scheduler_unload_recovery cluster

  Unloads a staged recovery.
  """
  options = app.get_options()
  check_and_log_response(AuroraClientAPI(CLUSTERS[cluster], options.verbosity)
      .unload_recovery())


@app.command
@requires.exactly('cluster')
def scheduler_list_job_updates(cluster):
  """usage: scheduler_list_job_updates cluster

  Lists in-flight job updates.
  """
  options = app.get_options()
  resp = AuroraClientAPI(CLUSTERS[cluster], options.verbosity).get_job_updates()
  check_and_log_response(resp)
  print('Role\tEnv\tJob')
  for update in resp.jobUpdates:
    print('%s\t%s\t%s' % (
      update.jobKey.role if update.jobKey else update.roleDeprecated,
      update.jobKey.environment if update.jobKey else None,
      update.jobKey.name if update.jobKey else update.jobDeprecated))


@app.command
@requires.exactly('cluster')
def scheduler_snapshot(cluster):
  """usage: scheduler_snapshot cluster

  Request that the scheduler perform a storage snapshot and block until complete.
  """
  options = app.get_options()
  check_and_log_response(AuroraClientAPI(CLUSTERS['cluster'], options.verbosity).snapshot())
