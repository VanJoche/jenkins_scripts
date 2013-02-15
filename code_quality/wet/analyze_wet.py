#!/usr/bin/env python
import os
import sys
sys.path.append('%s/jenkins_scripts'%os.environ['WORKSPACE'])
import subprocess
import string
import fnmatch
import shutil
import optparse
from common import *
from time import sleep


def get_environment2():
    my_env = os.environ
    my_env['WORKSPACE'] = os.getenv('WORKSPACE', '')
    my_env['INSTALL_DIR'] = os.getenv('INSTALL_DIR', '')
    #my_env['HOME'] = os.getenv('HOME', '')
    my_env['HOME'] = os.path.expanduser('~')
    my_env['JOB_NAME'] = os.getenv('JOB_NAME', '')
    my_env['BUILD_NUMBER'] = os.getenv('BUILD_NUMBER', '')
    my_env['ROS_TEST_RESULTS_DIR'] = os.getenv('ROS_TEST_RESULTS_DIR', my_env['WORKSPACE']+'/test_results')
    my_env['PWD'] = os.getenv('WORKSPACE', '')
    #my_env['ROS_PACKAGE_MIRROR'] = 'http://packages.ros.org/ros/ubuntu'
    my_env['ROS_PACKAGE_MIRROR'] = 'http://apt-mirror/packages/ros'
    
    return my_env

def analyze_wet(ros_distro, repo_list, version_list, workspace, test_depends_on):
    print "Testing on distro %s"%ros_distro
    print "Testing repositories %s"%', '.join(repo_list)
    print "Testing versions %s"%', '.join(version_list)
    if test_depends_on:
        print "Testing depends-on"
    else:
        print "Not testing depends on"

    # clean up old tmp directory
    shutil.rmtree(os.path.join(workspace, 'tmp'), ignore_errors=True)

    # set directories
    tmpdir = os.path.join('/tmp', 'test_repositories')
    repo_sourcespace = os.path.join(tmpdir, 'src_repository')
    dependson_sourcespace = os.path.join(tmpdir, 'src_depends_on')
    repo_buildspace = os.path.join(tmpdir, 'build_repository')
    dependson_buildspace = os.path.join(tmpdir, 'build_depend_on')

    # Add ros sources to apt
    print "Add ros sources to apt"
    with open('/etc/apt/sources.list.d/ros-latest.list', 'w') as f:
        f.write("deb http://packages.ros.org/ros-shadow-fixed/ubuntu %s main"%os.environ['OS_PLATFORM'])
    call("wget http://packages.ros.org/ros.key -O %s/ros.key"%workspace)
    call("apt-key add %s/ros.key"%workspace)
    call("apt-get update")

    # install stuff we need
    print "Installing Debian packages we need for running this script"
    call("apt-get install python-catkin-pkg python-rosinstall python-rosdistro --yes")
    import rosdistro

    # parse the rosdistro file
    print "Parsing rosdistro file for %s"%ros_distro
    distro = rosdistro.RosDistro(ros_distro)
    print "Parsing devel file for %s"%ros_distro
    devel = rosdistro.DevelDistro(ros_distro)

    # Create rosdep object
    print "Create rosdep object"
    rosdep = RosDepResolver(ros_distro)

    # download the repo_list from source
    print "Creating rosinstall file for repo list"
    rosinstall = ""
    for repo, version in zip(repo_list, version_list):
        if version == 'devel':
            if not devel.repositories.has_key(repo):
                raise BuildException("Repository %s does not exist in Devel Distro"%repo)
            print "Using devel distro file to download repositories"
            rosinstall += devel.repositories[repo].get_rosinstall()
        else:
            if not distro.get_repositories().has_key(repo):
                raise BuildException("Repository %s does not exist in Ros Distro"%repo)
            if version in ['latest', 'master']:
                print "Using latest release distro file to download repositories"
                rosinstall += distro.get_rosinstall(repo, version='master')
            else:
                print "Using version %s of release distro file to download repositories"%version
                rosinstall += distro.get_rosinstall(repo, version)
    print "rosinstall file for all repositories: \n %s"%rosinstall
    with open(os.path.join(workspace, "repo.rosinstall"), 'w') as f:
        f.write(rosinstall)
    print "Install repo list from source"
    os.makedirs(repo_sourcespace)
    call("rosinstall %s %s/repo.rosinstall --catkin"%(repo_sourcespace, workspace))

    # get the repositories build dependencies
    print "Get build dependencies of repo list"
    repo_build_dependencies = get_dependencies(repo_sourcespace, build_depends=True, test_depends=False)
    print "Install build dependencies of repo list: %s"%(', '.join(repo_build_dependencies))
    apt_get_install(repo_build_dependencies, rosdep)

    # replace the CMakeLists.txt file for repositories that use catkin
    print "Removing the CMakeLists.txt file generated by rosinstall"
    os.remove(os.path.join(repo_sourcespace, 'CMakeLists.txt'))
    print "Create a new CMakeLists.txt file using catkin"
    ros_env = get_ros_env('/opt/ros/%s/setup.bash'%ros_distro)
    call("catkin_init_workspace %s"%repo_sourcespace, ros_env)
    os.makedirs(repo_buildspace)
    os.chdir(repo_buildspace)
    call("cmake %s -DCMAKE_TOOLCHAIN_FILE=/opt/ros/groovy/share/ros/core/rosbuild/rostoolchain.cmake"%repo_sourcespace, ros_env)
    ros_env_repo = get_ros_env(os.path.join(repo_buildspace, 'devel/setup.bash'))

    # build repositories
    print "Build repo list"
    print "CMAKE_PREFIX_PATH: %s"%ros_env['CMAKE_PREFIX_PATH']
    call("make", ros_env)


    # Concatenate filelists
    print '-----------------  Concatenate filelists -----------------  '
    filelist = '%s'%repo_buildspace + '/filelist.lst'
    helper = subprocess.Popen(('%s/jenkins_scripts/code_quality/concatenate_filelists.py --dir %s --filelist %s'%(workspace,repo_buildspace, filelist)).split(' '), env=os.environ)
    helper.communicate()
    print '////////////////// cma analysis done ////////////////// \n\n'

    # Run CMA
    print '-----------------  Run CMA analysis -----------------  '
    cmaf = repo_sourcespace#repo_buildspace
    helper = subprocess.Popen(('pal QACPP -cmaf %s -list %s'%(cmaf, filelist)).split(' '), env=os.environ)
    helper.communicate()
    print '////////////////// cma analysis done ////////////////// \n\n'

    # Export metrics to yaml and csv files
    uri= 'uri'
    uri_info= 'uri_info'
    vcs_type= 'vcs_type'
    print '-----------------  Export metrics to yaml and csv files ----------------- '
    helper = subprocess.Popen(('%s/jenkins_scripts/code_quality/wet/export_metrics_to_yaml_wet.py --path %s --path_src %s --doc doc --csv csv --config %s/jenkins_scripts/code_quality/export_config.yaml --distro %s --stack %s --uri %s --uri_info %s --vcs_type %s'%(workspace, repo_buildspace, repo_sourcespace, workspace, ros_distro, repo_list, uri,  uri_info, vcs_type)).split(' '), env=os.environ)
    helper.communicate()
    print '////////////////// export metrics to yaml and csv files done ////////////////// \n\n'     
 
    # Push results to server
    print '-----------------  Push results to server -----------------  '
    helper = subprocess.Popen(('%s/jenkins_scripts/code_quality/wet/push_results_to_server_wet.py --path %s --doc doc --path_src %s --meta_package %s'%(workspace, repo_buildspace, repo_sourcespace, repo_list)).split(' '), env=os.environ)
    helper.communicate()
    print '////////////////// push results to server done ////////////////// \n\n' 

    # Upload results to QAVerify
    #print ' -----------------  upload results to QAVerify -----------------  '
    #helper = subprocess.Popen(('%s/jenkins_scripts/code_quality/wet/upload_to_QAVerify_wet.py --path %s --snapshot %s'%(workspace, workspace, snapshots_path)).split(' '), env=os.environ)
    #helper.communicate()
    #print '////////////////// upload results to QAVerify done ////////////////// \n\n'      


    # copy #TODO: rm
    shutil.rmtree(os.path.join(workspace, 'test_results'), ignore_errors=True)
    os.makedirs(os.path.join(workspace, 'test_results'))
    call("cp -r %s %s/test_results/build_repository"%(repo_buildspace, workspace))
    call("cp -r %s %s/test_results/source_repository"%(repo_sourcespace, workspace))


def main():
    parser = optparse.OptionParser()
    parser.add_option("--depends_on", action="store_true", default=False)
    (options, args) = parser.parse_args()

    if len(args) <= 2 or len(args)%2 != 1:
        print "Usage: %s ros_distro repo1 version1 repo2 version2 ..."%sys.argv[0]
        print " - with ros_distro the name of the ros distribution (e.g. 'fuerte' or 'groovy')"
        print " - with repo the name of the repository"
        print " - with version 'latest', 'devel', or the actual version number (e.g. 0.2.5)."
        raise BuildException("Wrong arguments for analyze_wet script")

    ros_distro = args[0]

    repo_list = [args[i] for i in range(1, len(args), 2)]
    version_list = [args[i+1] for i in range(1, len(args), 2)]
    workspace = os.environ['WORKSPACE']

    print "Running analyze_wet test on distro %s and repositories %s"%(ros_distro,
                                                                      ', '.join(["%s (%s)"%(r,v) for r, v in zip(repo_list, version_list)]))
    analyze_wet(ros_distro, repo_list, version_list, workspace, test_depends_on=options.depends_on)



if __name__ == '__main__':
    # global try
    try:
        main()
        print "analyze_wet script finished cleanly"

    # global catch
    except BuildException as ex:
        print ex.msg

    except Exception as ex:
        print "analyze_wet script failed. Check out the console output above for details."
        raise ex
