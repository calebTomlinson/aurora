package com.twitter.mesos.scheduler.storage.db.migrations.v0_v1;

import java.io.IOException;

import com.google.inject.Inject;

import com.twitter.mesos.gen.Identity;
import com.twitter.mesos.gen.JobConfiguration;
import com.twitter.mesos.gen.ScheduledTask;
import com.twitter.mesos.gen.TwitterTaskInfo;
import com.twitter.mesos.scheduler.storage.StorageRole;
import com.twitter.mesos.scheduler.storage.StorageRole.Role;
import com.twitter.mesos.scheduler.storage.db.DbStorage;
import com.twitter.mesos.scheduler.storage.db.migrations.SchemaMigrator;

/**
 * Migrates old style thrift struct owner strings to storage v1 style {@link Identity} owners.
 *
 * @author John Sirois
 */
public class OwnerMigrator extends SchemaMigrator {

  @Inject
  public OwnerMigrator(@StorageRole(Role.Legacy) DbStorage from,
      @StorageRole(Role.Primary) DbStorage to) throws IOException {
    super(from, "pre-migrate-legacy.sql", null, to, null, null);
  }

  @Override
  public ScheduledTask migrateTask(ScheduledTask task) {
    ScheduledTask migrated = task.deepCopy();
    migrated.getAssignedTask().getTask().setOwner(getOwner(task));
    return migrated;
  }

  @Override
  public JobConfiguration migrateJobConfig(JobConfiguration jobConfiguration) {
    JobConfiguration migrated = jobConfiguration.deepCopy();
    migrated.setOwner(getOwner(jobConfiguration));
    for (TwitterTaskInfo taskInfo : migrated.getTaskConfigs()) {
      taskInfo.setOwner(getOwner(taskInfo));
    }
    return migrated;
  }

  public static Identity getOwner(JobConfiguration jobConfiguration) {
    return repairIdentity(jobConfiguration.getOldOwner(), jobConfiguration.getOwner());
  }

  public static Identity getOwner(ScheduledTask scheduledTask) {
    return getOwner(scheduledTask.getAssignedTask().getTask());
  }

  public static Identity getOwner(TwitterTaskInfo task) {
    return repairIdentity(task.getOldOwner(), task.getOwner());
  }

  private static Identity repairIdentity(String oldOwner, Identity owner) {
    return owner != null ? owner : new Identity(oldOwner, oldOwner);
  }
}