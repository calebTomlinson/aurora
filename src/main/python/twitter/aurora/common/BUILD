python_library(
  name = 'aurora_job_key',
  sources = ['aurora_job_key.py'],
  dependencies = [
    pants('aurora/twitterdeps/src/python/twitter/common/lang'),
    pants('src/main/thrift/com/twitter/aurora/gen:py-thrift'),
  ]
)

python_library(
  name = 'cluster',
  sources = ['cluster.py'],
  dependencies = [
    pants('src/main/python/twitter/aurora/BUILD.thirdparty:pystachio'),
  ]
)

python_library(
  name = 'clusters',
  sources = ['clusters.py'],
  dependencies = [
    pants(':cluster'),
    pants('src/main/python/twitter/aurora/BUILD.thirdparty:pystachio'),
    pants('aurora/twitterdeps/src/python/twitter/common/collections'),
  ]
)

python_library(
  name = 'cluster_option',
  sources = ['cluster_option.py'],
  dependencies = [
    pants('aurora/twitterdeps/src/python/twitter/common/lang'),
  ]
)

python_library(
  name = 'http_signaler',
  sources = ['http_signaler.py'],
  dependencies = [
    pants('src/main/python/twitter/aurora/BUILD.thirdparty:socksipy-branch'),  # TODO(wickman) MESOS-3710
    pants('aurora/twitterdeps/src/python/twitter/common/lang'),
    pants('aurora/twitterdeps/src/python/twitter/common/log'),
  ]
)

python_library(
  name = 'common',
  dependencies = [
    pants(':aurora_job_key'),
    pants(':cluster'),
    pants(':cluster_option'),
    pants(':clusters'),
    pants(':http_signaler'),
  ],
  provides = setup_py(
    name = 'twitter.aurora.common',
    version = '0.1.0',
    description = 'Aurora common libraries.',
    license = 'Apache License, Version 2.0',
  )
)
