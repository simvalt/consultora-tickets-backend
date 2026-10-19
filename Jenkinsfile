def backendPhase(String phase) {
    withEnv(["TICKETS_PHASE=${phase}"]) {
        sh '''#!/usr/bin/env bash
set -Eeuo pipefail
sudo -n -u simonvalt /home/simonvalt/tickets-demo/automation/run.sh backend "$TICKETS_COMMIT" "$TICKETS_PHASE" "$BUILD_NUMBER"
'''
    }
}

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
            steps {
                checkout scm
                script {
                    env.TICKETS_COMMIT = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                }
            }
        }
        stage('Preparar release') {
            steps { script { backendPhase('prepare') } }
        }
        stage('Lint') {
            steps { script { backendPhase('lint') } }
        }
        stage('Auditar dependencias') {
            steps { script { backendPhase('audit') } }
        }
        stage('Construir imagenes') {
            steps { script { backendPhase('build') } }
        }
        stage('Respaldar PostgreSQL') {
            steps { script { backendPhase('backup') } }
        }
        stage('Aplicar migraciones') {
            steps { script { backendPhase('migrate') } }
        }
        stage('Desplegar API') {
            steps { script { backendPhase('deploy') } }
        }
        stage('Verificar salud') {
            steps { script { backendPhase('health') } }
        }
        stage('Verificar aislamiento') {
            steps { script { backendPhase('verify') } }
        }
    }
    post {
        always {
            script {
                if (env.TICKETS_COMMIT) {
                    backendPhase('finish')
                }
            }
        }
    }
}
