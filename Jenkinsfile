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
                script {
                    def deployStatus = sh(returnStatus: true, script: '''#!/usr/bin/env bash
set -Eeuo pipefail
commit="$(git rev-parse HEAD)"
sudo -n -u simonvalt /home/simonvalt/tickets-demo/automation/run.sh backend "$commit"
''')
                    if (deployStatus == 42) {
                        unstable('Tickets desplegado correctamente; hay pruebas fallidas no bloqueantes. Revisar el registro.')
                    } else if (deployStatus != 0) {
                        error("No se completo el despliegue de Tickets (codigo ${deployStatus}).")
                    }
                }
            }
        }
    }
}
