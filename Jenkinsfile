pipeline {
    agent { label 'servidor1' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
        timeout(time: 35, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }
    triggers { pollSCM('H/5 * * * *') }
    stages {
        stage('Checkout main') {
            steps { checkout scm }
        }
        stage('Pruebas y despliegue aislado') {
            steps {
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
commit="$(git rev-parse HEAD)"
sudo -n -u simonvalt /home/simonvalt/tickets-demo/automation/run.sh backend "$commit"
'''
            }
        }
    }
}
