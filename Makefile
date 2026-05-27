docker-build:
	git pull
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 739561048503.dkr.ecr.us-east-1.amazonaws.com
	docker build -t 739561048503.dkr.ecr.us-east-1.amazonaws.com/roboshop-payment:$(image_tag) .
	trivy image 739561048503.dkr.ecr.us-east-1.amazonaws.com/roboshop-payment:$(image_tag) -s CRITICAL,HIGH --ignore-unfixed
	docker push 739561048503.dkr.ecr.us-east-1.amazonaws.com/roboshop-payment:$(image_tag)

argocd-deploy:
	argocd login $(argocd_server) --skip-test-tls --username admin --password $(argocd_admin_password)
	argocd app create roboshop-payment --sync-policy auto --upsert --repo https://github.com/raghudevopsb88/roboshop-helm-v1.git --path . --dest-server https://kubernetes.default.svc --dest-namespace default --helm-set-string image_tag=$(image_tag) --values values/roboshop-payment.yml
