#!/bin/bash -e
USAGE="Usage: ./install.sh ENVIRONMENT VAULT_ROLE_ID_PATH VAULT_SECRET_ID"
ENVIRONMENT=${1:?$USAGE}
export VAULT_ROLE_ID_PATH=${2:?$USAGE}
echo "VAULT_ROLE_ID_PATH=${VAULT_ROLE_ID_PATH}"
export VAULT_SECRET_ID=${3:?$USAGE}
echo "VAULT_SECRET_ID=${VAULT_SECRET_ID}"
export VAULT_ADDR=https://vault.slac.stanford.edu
VAULT_PATH_PREFIX=$(cat ../environments/values-$ENVIRONMENT.yaml | grep vaultPathPrefix | awk '{print $2}')
ARGOCD_PASSWORD=`vault kv get --field=argocd.admin.plaintext_password $VAULT_PATH_PREFIX/argocd`

GIT_URL=$( git config --get remote.origin.url )
HTTP_URL=$( echo "$GIT_URL" | sed s%git@%https://% | sed s%github.com:%github.com/% ).git
GIT_BRANCH=${GITHUB_HEAD_REF:-`git branch --show-current`}

echo "Set VAULT_TOKEN in a secret for vault-secrets-operator..."
# The namespace may not exist already, but don't error if it does.
kubectl create ns vault-secrets-operator || true
kubectl create secret generic vault-secrets-operator \
  --namespace vault-secrets-operator \
  --from-literal=VAULT_ROLE_ID=$(vault read --format=json ${VAULT_ROLE_ID_PATH}/role-id | jq -r .data.role_id | sed 's/"//g') \
  --from-literal=VAULT_SECRET_ID=${VAULT_SECRET_ID} \
  --from-literal=VAULT_TOKEN_MAX_TTL=600 \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Update / install vault-secrets-operator..."
# ArgoCD depends on pull-secret, which depends on vault-secrets-operator.
helm dependency update ../applications/vault-secrets-operator
echo "Resolved helm dependency update"
helm upgrade vault-secrets-operator ../applications/vault-secrets-operator \
  --install \
  --values ../applications/vault-secrets-operator/values.yaml \
  --values ../applications/vault-secrets-operator/values-$ENVIRONMENT.yaml \
  --create-namespace \
  --namespace vault-secrets-operator \
  --timeout 5m \
  --debug \
  --wait

echo "Update / install argocd using helm..."
helm dependency update ../applications/argocd
helm upgrade argocd ../applications/argocd \
  --install \
  --values ../applications/argocd/values.yaml \
  --values ../applications/argocd/values-$ENVIRONMENT.yaml \
  --set global.vaultSecretsPath="$VAULT_PATH_PREFIX" \
  --create-namespace \
  --namespace argocd \
  --timeout 5m \
  --debug \
  --wait

#echo "Login to argocd..."
#argocd login \
#  --plaintext \
#  --port-forward \
#  --port-forward-namespace argocd \
#  --username admin \
#  --password $ARGOCD_PASSWORD

#echo "Creating top level application"
#argocd app create science-platform \
#  --repo $GIT_URL \
#  --path environments --dest-namespace default \
#  --dest-server https://kubernetes.default.svc \
#  --upsert \
#  --revision $GIT_BRANCH \
#  --port-forward \
#  --port-forward-namespace argocd \
#  --helm-set repoURL=$GIT_URL \
#  --helm-set targetRevision=$GIT_BRANCH \
#  --values values-$ENVIRONMENT.yaml

#argocd app sync science-platform \
#  --port-forward \
#  --port-forward-namespace argocd

