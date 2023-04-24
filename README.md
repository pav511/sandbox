# sandbox

Various notes

vault write 

Deploy sandbox-dev to k8s-sandbox vcluster

// get approle role-id generated for deployment
  vault read auth/approle/role/sandbox-dev.slac.stanford.edu/role-id
// if necessary delete previous failed deploy
 k delete namespace -R vault-secrets-operator
 k delete namespace -R argocd
// approle secret-id expires after one use, so generate a new one
 vault write -f auth/approle/role/sandbox-dev.slac.stanford.edu/secret-id
// login with approle
 vault write auth/approle/login role_id=‘REDACTED' secret_id=‘REDACTED'
 
// run install script 
 ./install.sh usdfdev auth/approle/role/sandbox-dev.slac.stanford.edu REDACTED-SECRED-ID

check external ip on argocd

k get svc -n argocd

