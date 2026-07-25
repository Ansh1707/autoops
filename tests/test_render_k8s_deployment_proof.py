from scripts import render_k8s_deployment_proof


def test_render_k8s_deployment_proof_uses_local_images_and_small_replicas():
    resources = render_k8s_deployment_proof.render_resources()
    deployments = {
        resource["metadata"]["name"]: resource
        for resource in resources
        if resource.get("kind") == "Deployment"
    }

    assert deployments["api"]["spec"]["replicas"] == 1
    assert deployments["worker"]["spec"]["replicas"] == 1
    assert deployments["frontend"]["spec"]["replicas"] == 1

    api_container = deployments["api"]["spec"]["template"]["spec"]["containers"][0]
    worker_container = deployments["worker"]["spec"]["template"]["spec"]["containers"][0]
    beat_container = deployments["beat"]["spec"]["template"]["spec"]["containers"][0]
    frontend_container = deployments["frontend"]["spec"]["template"]["spec"]["containers"][0]

    assert api_container["image"] == "autoops-api:ci"
    assert worker_container["image"] == "autoops-worker:ci"
    assert beat_container["image"] == "autoops-worker:ci"
    assert frontend_container["image"] == "autoops-frontend:ci"
    assert api_container["imagePullPolicy"] == "Never"


def test_render_k8s_deployment_proof_writes_manifest(tmp_path):
    output = tmp_path / "autoops-kind.yaml"

    assert render_k8s_deployment_proof.main(["--output", str(output)]) == 0

    text = output.read_text(encoding="utf-8")
    assert "autoops-api:ci" in text
    assert "autoops-worker:ci" in text
    assert "autoops-frontend:ci" in text
