from flask import Flask, request, jsonify
import subprocess
import yaml
import os
import json
import re

app = Flask(__name__)

# List of required fields for the JSON file validation
REQUIRED_JSON_FIELDS = [
    "Software Entity UID", "Total Energy Consumption", "Total Execution Time",
    "node type", "node CPU cores", "node memory size",
    "node persistent storage size", "node network bandwidth upload"
]


ALLOWED_NODE_TYPES = {"server", "edge_device"}


ALLOWED_WORDS = {"LOW", "HIGH", "INDIFFERENT"}


COMPARISON_PATTERN = r"^(<|>|=)\d+(\.\d+)?$"



def validate_field_value(value, valid_step_names, field_name=None):

    if field_name == "node type" and value in ALLOWED_NODE_TYPES:
        return True  


    if isinstance(value, str):

        if field_name == "Software Entity UID" and value in valid_step_names:
            return True


        if value.upper() in ALLOWED_WORDS:
            return True


        if re.match(COMPARISON_PATTERN, value):
            return True

    return False



def extract_step_names(yaml_content):
    try:
        workflow = yaml.safe_load(yaml_content)
        steps = workflow.get("spec", {}).get("templates", [])

        step_names = []
        for template in steps:
            if 'steps' in template:
                for step in template['steps']:
                    if isinstance(step, list) and step:
                        for task in step:
                            if isinstance(task, dict) and "name" in task:
                                step_names.append(task["name"])
        print(step_names)
        return step_names
    except yaml.YAMLError as exc:
        return f"Error parsing YAML file: {exc}"



def validate_json_file(json_content, valid_step_names):
    try:
        data = json.loads(json_content)
    except json.JSONDecodeError as exc:
        return f"Error parsing JSON file: {exc}", False

    missing_fields = []
    invalid_fields = []
    invalid_uid_steps = []

    for step in data:
        for field in REQUIRED_JSON_FIELDS:
            if field not in step:
                missing_fields.append(field)
            else:
                if field == "Software Entity UID":
                    if not validate_field_value(step[field], valid_step_names, field):
                        invalid_uid_steps.append(step[field])
                elif field == "node type":
                    if not validate_field_value(step[field], valid_step_names, field):
                        invalid_fields.append(f"{field}: {step[field]}")
                else:
                    if not validate_field_value(step[field], valid_step_names):
                        invalid_fields.append(f"{field}: {step[field]}")

    if missing_fields:
        return f"Missing fields in JSON file: {', '.join(missing_fields)}", False
    if invalid_fields:
        return f"Invalid field values in JSON file: {', '.join(invalid_fields)}", False
    if invalid_uid_steps:
        return f"Invalid Software Entity UID(s): {', '.join(invalid_uid_steps)}", False

    return "JSON file is valid", True



def add_node_selectors(workflow, json_data):
    steps = workflow.get("spec", {}).get("templates", [])

    # Iterate over each step in the workflow
    for template in steps:
        if 'steps' in template:
            for step in template['steps']:
                if isinstance(step, list) and step:
                    for task in step:
                        step_name = task.get("name")
                        if step_name:
                            # Find the corresponding step in the JSON data
                            matching_json_step = next(
                                (item for item in json_data if item["Software Entity UID"] == step_name),
                                None
                            )
                            if matching_json_step:
                                # Check the node type and add the appropriate node selector
                                node_type = matching_json_step.get("node type")
                                if node_type:
                                    # Now we need to add the nodeSelector to the associated template
                                    template_name = task.get("template")
                                    for tmpl in steps:
                                        if tmpl.get("name") == template_name:
                                            if node_type == "server":
                                                tmpl["nodeSelector"] = {"server": "true"}
                                            elif node_type == "edge_device":
                                                tmpl["nodeSelector"] = {"edge_device": "true"}

    return workflow



def submit_argo_workflow_from_yaml(yaml_content, json_data):
    try:
        workflow = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        return f"Error parsing YAML file: {exc}"

    # Add node selectors based on the JSON data
    updated_workflow = add_node_selectors(workflow, json_data)

    workflow_file = "/tmp/workflow.yaml"
    with open(workflow_file, "w") as f:
        yaml.dump(updated_workflow, f)

    result = subprocess.run(["argo", "submit", workflow_file], capture_output=True, text=True)
    print(f"Argo command output: {result.stdout}")
    print(f"Argo command error: {result.stderr}")

    os.remove(workflow_file)

    if result.returncode == 0:
        return result.stdout
    else:
        return result.stderr



@app.route('/run-workflow', methods=['POST'])
def run_workflow():
    if 'yaml_file' not in request.files or 'json_file' not in request.files:
        return jsonify({"error": "Both YAML and JSON files are required."}), 400

    yaml_file = request.files['yaml_file']

    if yaml_file.filename != 'workflow.yaml':
        return jsonify({"error": "YAML file must be named 'workflow.yaml'."}), 400

    json_file = request.files['json_file']

    if json_file.filename != 'app_res.json':
        return jsonify({"error": "JSON file must be named 'app_res.json'."}), 400

    yaml_content = yaml_file.read()
    json_content = json_file.read()

    valid_step_names = extract_step_names(yaml_content)
    if isinstance(valid_step_names, str):
        return jsonify({"error": valid_step_names}), 400

    print("Extracted valid step names:", valid_step_names)

    json_validation_message, is_valid_json = validate_json_file(json_content, valid_step_names)
    if not is_valid_json:
        return jsonify({"error": json_validation_message}), 400

    json_data = json.loads(json_content)

    output = submit_argo_workflow_from_yaml(yaml_content, json_data)

    return jsonify({"message": "Workflow submitted successfully.", "argo_output": output,
                    "json_validation": json_validation_message}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

