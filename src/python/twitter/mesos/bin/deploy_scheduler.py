#!/usr/bin/env python2.6
import os
import subprocess
import sys
import time

from git import *

from optparse import OptionParser
from time import gmtime, strftime

from twitter.mesos import clusters
from twitter.mesos.tunnel_helper import TunnelHelper

__author__ = 'William Farner'

REMOTE_USER = 'mesos'

TEST_CMD = './pants %s clean-all test'
TEST_TARGETS = ['tests/java/com/twitter/mesos:all-tests!']

BUILD_TARGET_CMDS = [
  './pants src/java/com/twitter/mesos/scheduler! zip',
  './pants src/java/com/twitter/mesos/executor! zip',
  './pants src/python/twitter/mesos:process_scraper!'
]

STAGE_DIR = '~/release_staging'

SCHEDULER_PACKAGE = 'mesos-scheduler.zip'
BUILD_SCHEDULER_PACKAGE_PATH = 'dist/%s' % SCHEDULER_PACKAGE
BUILD_SCHEDULER_JAR_PATH = 'dist/mesos-scheduler-bundle/mesos-scheduler.jar'
STAGED_PACKAGE_PATH = '%s/%s' % (STAGE_DIR, SCHEDULER_PACKAGE)

DC_WILDCARD = '$dc'
CLUSTER_WILDCARD = '$cluster'
HDFS_BIN_DIR = '/mesos/pkg/mesos/bin'
HDFS_BIN_FILES = {
  'mesos/scripts/executor.sh': '%s/$cluster/$dc-$cluster-executor.sh' % HDFS_BIN_DIR,
  'dist/mesos-executor.zip':  '%s/$cluster/mesos-executor.zip' % HDFS_BIN_DIR,
  'dist/process_scraper.pex':  '%s/$cluster/process_scraper.pex' % HDFS_BIN_DIR,
}

MESOS_HOME = '/usr/local/mesos'
LIVE_BUILD_PATH = '%s/current' % MESOS_HOME
RELEASES_DIR = '%s/releases' % MESOS_HOME

SCHEDULER_HTTP = 'http://localhost:8081'

options = None

def get_cluster_dc():
  return clusters.get_dc(options.cluster)


def get_cluster_name():
  return clusters.get_local_name(options.cluster)


def get_scheduler_role():
  return clusters.get_scheduler_role(options.cluster)


def get_cluster_dc():
  return clusters.get_dc(options.cluster)


def get_scheduler_machines():
  if options.all_hosts:
    if options.really_push:
      params = dict(
        dc = get_cluster_dc(),
        role = get_scheduler_role()
      )
      result, (output, _) = run_cmd([
        'ssh', TunnelHelper.get_tunnel_host(options.cluster),
        'loony --dc=%(dc)s --group=role:%(role)s --one-column' % params
      ])
      if result != 0:
        sys.exit("Failed to determine scheduler hosts for dc: %(dc)s role: %(role)s" % params)
      return [host.strip() for host in output.splitlines()]
    else:
      return ['[dummy-host1]', '[dummy-host2]', '[dummy-host3]']
  else:
    return [clusters.get_scheduler_host(options.cluster)]


def read_bool_stdin(prompt, default=None):
  if default is not None:
    if default:
      prompt = '%s [y] ' % prompt
    else:
      prompt = '%s [n] ' % prompt
  while True:
    result = raw_input('%s ' % prompt).lower()
    if result:
      return result in ["yes", "y", "true", "t", "1"]
    elif default is not None:
      return default
    else:
      print "I'll keep asking until you answer!"


def maybe_run_command(runner, cmd):
  if options.verbose or not options.really_push:
    print '%s command: %s' % ('Executing' if options.really_push else 'Would run', ' '.join(cmd))
  if options.really_push:
    return runner(cmd)


def check_call(cmd):
  """Wrapper for subprocess.check_call."""
  maybe_run_command(subprocess.check_call, cmd)


def run_cmd(cmd):
  """Runs a command and returns its return code along with stderr/stdout tuple"""
  def fork_join(args):
    proc = subprocess.Popen(args, stdout = subprocess.PIPE, stderr = subprocess.PIPE)
    output = proc.communicate()
    return proc.returncode, output
  return maybe_run_command(fork_join, cmd)


def ssh_target(host):
  return '%s@%s' % (REMOTE_USER, host)


def remote_call(host, cmd):
  return run_cmd(['ssh', ssh_target(host)] + cmd)


def fetch_scheduler_http(host, endpoint):
  result = remote_call(host, ['curl', '--silent', '%s/%s' % (SCHEDULER_HTTP, endpoint)])
  if result is not None:
    return result[1][0].strip()


def cmd_output(cmd):
  """Runs a command and returns only its stdout"""
  result = run_cmd(cmd)
  if result:
    returncode, output = result
    return output[0].strip()
  else:
    return None


def check_output(cmd):
  """Stand-in for subprocess.check_output, added in python 2.7"""
  result = run_cmd(cmd)
  if result is not None:
    returncode, output = result
    assert returncode == 0, 'Command failed: "%s", output %s' % (' '.join(cmd), output)
    return output


def remote_check_call(host, cmd):
  check_output(['ssh', ssh_target(host)] + cmd)


def check_tag(tag, check_on_master=True):
  """
    Checks that the given tag is valid on origin to enable repeatable builds and returns the sha
    the tag points to.
  """
  repo = Repo()
  if check_on_master:
    if repo.active_branch.name != 'master':
      print >> sys.stderr, 'Deploys must be from master'
      sys.exit(1)

  # TODO(John Sirois): leverage the repo api here where possible
  failed, _ = run_cmd(['git', 'ls-remote', '--exit-code', '--tags', 'origin', tag])
  if failed:
    print >> sys.stderr, 'The tag %s must be on origin' % tag
    sys.exit(1)

  # Find the sha of the commit this heavy-weight tag points to.
  failed, (sha, _) = run_cmd(['git', 'rev-list', '%(tag)s^..%(tag)s' % dict(tag = tag)])
  if failed:
    print >> sys.stderr, 'Failed to find the commit %s points to' % tag
    sys.exit(1)
  tag_sha = sha.strip()

  head_sha = repo.head.commit.hexsha
  if head_sha != tag_sha:
    print >> sys.stderr, 'Local repo is not at the expected sha, found %s - please reset' % head_sha
    sys.exit(1)

  return tag_sha


def build():
  for test_target in TEST_TARGETS:
    print 'Executing test target: %s' % test_target
    check_call((TEST_CMD % test_target).split(' '))
  for build_target_cmd in BUILD_TARGET_CMDS:
    print 'Executing build target: %s' % build_target_cmd
    check_call(build_target_cmd.split(' '))


def find_current_build(hosts):
  # TODO(John Sirois): consider loony -t
  current_builds = set()
  for host in hosts:
    # the linux machines do not have realpath installed - this is at least portable
    command = [
      'ssh',
      ssh_target(host),
      """"python -c 'import os; print os.path.realpath(\\"%s\\")'" """ % LIVE_BUILD_PATH
    ]
    # TODO(John Sirois): get this to work via remote_call
    result = maybe_run_command(lambda cmd: os.popen(' '.join(cmd)).read(), command)
    if result:
      current_build = result.strip()
      if current_build != LIVE_BUILD_PATH:
        current_builds.add(current_build)

  current_builds = filter(bool, current_builds)
  if not options.ignore_conflicting_builds and options.really_push and len(current_builds) != 1:
    sys.exit('Found conflicting current builds: %s please resolve manually' % current_builds)
  current_build = current_builds.pop() if options.really_push else None
  print 'Found current build: %s' % current_build
  return current_build


def replace_hdfs_file(host, local_file, hdfs_path):
  HADOOP_CONF_DIR = '/etc/hadoop/hadoop-conf-%s' % get_cluster_dc()
  BASE_HADOOP_CMD = ['hadoop', '--config', HADOOP_CONF_DIR, 'fs']

  remote_call(host, BASE_HADOOP_CMD + ['-mkdir', os.path.dirname(hdfs_path)])
  remote_call(host, BASE_HADOOP_CMD + ['-rm', hdfs_path])
  remote_check_call(host, BASE_HADOOP_CMD + ['-put', local_file, hdfs_path])


def stage_build(hosts):
  result = cmd_output(['bash', '-c',
    'unzip -c %s build.properties | grep build.git.revision' % BUILD_SCHEDULER_JAR_PATH
  ])
  if options.really_push:
    _, sha = result.split('=')
  else:
    sha = '[sha]'
  release_scheduler_path = '%s/%s-%s' % (RELEASES_DIR, strftime("%Y%m%d%H%M%S", gmtime()), sha)

  print 'Staging the build at: %s on:\n\t%s' % (release_scheduler_path, '\n\t'.join(hosts))

  # Stage release dirs on all hosts
  for host in hosts:
    remote_check_call(host, ['mkdir', '-p', STAGE_DIR])
    check_output(['scp', BUILD_SCHEDULER_PACKAGE_PATH, '%s:%s' % (ssh_target(host),
                                                                  STAGED_PACKAGE_PATH)])
    remote_check_call(host, ['bash', '-c',
      '"mkdir -p %(release_dir)s &&'
      ' unzip -d %(release_dir)s %(staged_package)s &&'
      ' chmod +x %(release_dir)s/scripts/*.sh"' % {
        'release_dir': release_scheduler_path,
        'staged_package': STAGED_PACKAGE_PATH,
      },
    ])

  # Finally stage the HDFS artifacts
  host = hosts[0]
  wildcards = {
    DC_WILDCARD: get_cluster_dc(),
    CLUSTER_WILDCARD: get_cluster_name()
  }
  for local_file, hdfs_target in HDFS_BIN_FILES.items():
    for wildcard, value in wildcards.items():
      local_file = local_file.replace(wildcard, value)
      hdfs_target = hdfs_target.replace(wildcard, value)
    print 'Sending local file from %s to HDFS %s' % (local_file, hdfs_target)
    stage_file = os.path.join(STAGE_DIR, os.path.basename(local_file))
    check_output(['scp', local_file, '%s:%s' % (ssh_target(host), stage_file)])
    replace_hdfs_file(host, stage_file, hdfs_target)

  return release_scheduler_path


def set_live_build(hosts, build_path):
  print 'Linking the new build on the scheduler'
  # TODO(John Sirois): consider loony
  for host in hosts:
    remote_check_call(host, ['bash', '-c',
      '"rm -f %(live_build)s &&'
      ' ln -s %(build)s %(live_build)s"' % {
        'live_build': LIVE_BUILD_PATH,
        'build': build_path
      }
    ])


def start_scheduler(hosts):
  print 'Starting the scheduler on %s' % hosts
  # TODO(John Sirois): consider loony
  for host in hosts:
    remote_check_call(host, ['sudo', 'monit', 'start', 'mesos-scheduler'])
  if options.really_push:
    print 'Waiting for the scheduler to start'
    time.sleep(5)


def get_scheduler_uptime_secs(host, sha):
  """Checks that the scheduler is up at the expected sha and returns the uptime in seconds if so."""

  if not options.really_push:
    return 0

  vars_blob = fetch_scheduler_http(host, 'vars')
  assert vars_blob is not None, 'Failed to fetch vars from scheduler'

  vars = {}
  for kv in (line.split(' ', 1) for line in vars_blob.split('\n')):
    if len(kv) == 2:
      k, v = kv
      vars[k] = v
    else:
      vars[kv] = None

  if sha:
    deployed_sha = vars.get('build_git_revision')
    assert deployed_sha == sha, \
        'Host %s is not on current build %s, has %s' % (host, sha, deployed_sha)
  return int(vars.get('jvm_uptime_secs', 0))


def is_scheduler_healthy(host):
  if options.really_push:
    return fetch_scheduler_http(host, 'health') == 'OK'
  else:
    return True


def stop_scheduler(hosts):
  # TODO(John Sirois): consider loony -t
  for host in hosts:
    print 'Stopping the scheduler'
    print 'Temporarily disabling monit for the scheduler'
    remote_check_call(host, ['sudo', 'monit', 'unmonitor', 'mesos-scheduler'])
    fetch_scheduler_http(host, 'quitquitquit')
    print 'Waiting for scheduler to stop cleanly'
    if options.really_push:
      time.sleep(5)
    print 'Stopping scheduler via monit'
    remote_check_call(host, ['sudo', 'monit', 'stop', 'mesos-scheduler'])


def watch_scheduler(host, sha, up_min_secs):
  print 'Watching scheduler'
  started = False
  watch_start = time.time()
  start_detected_at = 0
  last_uptime = 0
  # Wait at most three minutes.
  while started or (time.time() - watch_start) < 180:
    if is_scheduler_healthy(host):
      uptime = get_scheduler_uptime_secs(host, sha)
      if not options.really_push:
        print 'Skipping further health checks, since we are not pushing.'
        return True
      print 'Up and healthy for %s seconds' % uptime

      if started:
        if uptime < last_uptime:
          print 'Detected scheduler process restart after update (uptime %s)!' % uptime
          return False
        elif time.time() - start_detected_at > up_min_secs:
          print 'Scheduler has been up for at least %d seconds' % up_min_secs
          return True
      else:
        start_detected_at = time.time()

      started = True
      last_uptime = uptime
    elif started:
      print 'Scheduler stopped responding to health checks!'
      return False
    time.sleep(2)
  return False


def rollback(hosts, rollback_build):
  print 'Initiating rollback'
  set_live_build(hosts, rollback_build)
  start_scheduler(hosts)


def main():
  parser = OptionParser(usage = '%prog [options] tag')
  parser.add_option(
    '-v',
    dest='verbose',
    default=False,
    action='store_true',
    help='Verbose logging. (default: %default)')

  cluster_list = list(clusters.get_clusters())
  cluster_list.sort()
  parser.add_option(
    '--cluster',
    type = 'choice',
    choices = cluster_list,
    dest='cluster',
    help='Cluster to deploy the scheduler in (one of: %s)' % ', '.join(cluster_list))

  # TODO(John Sirois): Make this the default once HA log rolls out.
  parser.add_option(
    '--all-hosts',
    dest='all_hosts',
    default=False,
    action='store_true',
    help='Deploy scheduler to all hosts designated in loony. (default: %default)')

  parser.add_option(
    '--skip_build',
    dest='skip_build',
    default=False,
    action='store_true',
    help='Skip build and test, use the existing build. (default: %default)')

  parser.add_option(
    '--really_push',
    dest='really_push',
    default=False,
    action='store_true',
    help='Safeguard to prevent fat-fingering.  When false, only show commands but do not run them. '
         '(default: %default)')

  parser.add_option(
    '--ignore_release',
    dest='ignore_release',
    default=False,
    action='store_true',
    help='Ignores the vert release protocol (can only be used in test)')

  parser.add_option(
    '--hotfix',
    dest='hotfix',
    default=False,
    action='store_true',
    help='Indicates this is a hotfix deploy from a temporary release branch instead of a vert tag')

  parser.add_option(
    '--ignore_conflicting_builds',
    dest='ignore_conflicting_builds',
    default=False,
    action='store_true',
    help='Ignores conflicting builds')

  global options
  (options, args) = parser.parse_args()

  if not options.really_push:
    print '****************************************************************************************'
    print 'You are running in pretend mode.  None of the commands are actually executed!'
    print 'If you wish to push, add command line arg --really_push'
    print '****************************************************************************************'

  if not options.cluster:
    cluster_list = list(clusters.get_clusters())
    cluster_list.sort()
    print ('Please specify the cluster you would like to deploy to with\n\t--cluster %s'
           % cluster_list)
    return

  if options.ignore_release and options.cluster == 'smf1-test':
    sha = None
  else:
    if len(args) != 1:
      print 'You must specify the tag you intend to push'
      sys.exit(1)

    sha = check_tag(args[0], check_on_master=not options.hotfix)
    if not sha:
      sys.exit(1)

  if options.skip_build:
    print 'Warning - skipping build, using existing build at %s' % BUILD_SCHEDULER_PACKAGE_PATH
  else:
    build()

  all_schedulers = get_scheduler_machines()

  # Stage the build on all machines and shut all the schedulers down
  current_build = find_current_build(all_schedulers)
  new_build = stage_build(all_schedulers)
  stop_scheduler(all_schedulers)

  # Point to the new build and start all schedulers up
  # TODO(John Sirois): support a rolling restart once multi-scheduler is used in all enviornments
  set_live_build(all_schedulers, new_build)
  start_scheduler(all_schedulers)

  # TODO(John Sirois): find the leader and health check it for 45 seconds instead
  for scheduler in all_schedulers:
    if not watch_scheduler(scheduler, sha=sha, up_min_secs=15):
      print 'scheduler on %s not healthy' % leader
      stop_scheduler(all_schedulers)

      if current_build:
        rollback(all_schedulers, current_build)
        print 'Push rolled back.'
      else:
        print 'Push failed - no previous builds to roll back to.'
      sys.exit(1)

  if options.really_push:
    print 'Push successful!'
  else:
    print 'Fake push completed'

if __name__ == '__main__':
  main()
