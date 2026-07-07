import json

def get_ref_schema(ref, components):
    ref_name = ref.split("/")[-1]
    return components["schemas"].get(ref_name, {})

def resolve_schema(schema, components):
    if "$ref" in schema:
        return get_ref_schema(schema["$ref"], components)
    if "allOf" in schema:
        combined = {}
        for sub in schema["allOf"]:
            sub_resolved = resolve_schema(sub, components)
            if "properties" in sub_resolved:
                combined.setdefault("properties", {}).update(sub_resolved["properties"])
            if "required" in sub_resolved:
                combined.setdefault("required", []).extend(sub_resolved["required"])
        return combined
    return schema

def get_properties(schema, components):
    resolved = resolve_schema(schema, components)
    props = resolved.get("properties", {})
    required = resolved.get("required", [])
    result = []
    for k, v in props.items():
        v_res = resolve_schema(v, components)
        prop_type = v_res.get("type", "object")
        desc = v_res.get("description", "")
        if "anyOf" in v:
            # Usually optional or nullable
            types = [resolve_schema(sub, components).get("type", "object") for sub in v["anyOf"] if resolve_schema(sub, components).get("type") != "null"]
            if types:
                prop_type = types[0]
        result.append({
            "name": k,
            "type": prop_type,
            "required": k in required,
            "description": desc
        })
    return result

def format_type(t):
    if t == "string":
        return "string"
    if t == "integer":
        return "int"
    if t == "boolean":
        return "boolean"
    if t == "number":
        return "number"
    if t == "array":
        return "array"
    return "object"

def generate_mock_value(prop_name, prop_type):
    if prop_type == "string":
        if "id" in prop_name:
            return "c56a4180-65aa-42ec-a945-5fd21dec0538"
        return "string"
    if prop_type == "int" or prop_type == "integer":
        return 0
    if prop_type == "boolean":
        return False
    if prop_type == "number":
        return 0.0
    if prop_type == "array":
        return []
    return {}

with open("/tmp/openapi.json") as f:
    spec = json.load(f)

components = spec.get("components", {})
paths = spec.get("paths", {})

sections = []

tags_to_include = ["customers", "plans", "subscriptions", "projects", "subscription-pages", "invoices"]

for path, methods in paths.items():
    for method, op in methods.items():
        tags = op.get("tags", [])
        if not any(tag in tags_to_include for tag in tags):
            continue
            
        summary = op.get("summary", "Endpoint")
        desc = op.get("description", "")
        
        md = f"## {summary}\n\n"
        if desc:
            md += f"{desc}\n\n"
            
        md += f"### Endpoint\n`{method.upper()} {path}`\n\n"
        
        md += "### Headers\n\n"
        md += "| Header | Type | Description |\n"
        md += "| :--- | :--- | :--- |\n"
        md += "| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |\n"
        
        # Check if project scoped (depends on project header). In reality, many endpoints use X-Project-ID
        needs_project = False
        for param in op.get("parameters", []):
            if param.get("name") == "X-Project-ID" and param.get("in") == "header":
                needs_project = True
        
        if method.upper() in ["POST", "PUT", "PATCH"]:
            md += "| `Content-Type` | `string` | Set value to `application/json`. |\n"
        if needs_project:
            md += "| `X-Project-ID` | `string` | The UUID of the project. |\n"
            
        md += "\n"
        
        req_body = op.get("requestBody", {})
        content = req_body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        
        body_props = []
        if schema:
            body_props = get_properties(schema, components)
            
        if body_props:
            md += "### Body Parameters\n\n"
            md += "| Field | Type | Required | Description |\n"
            md += "| :--- | :--- | :--- | :--- |\n"
            for p in body_props:
                req_str = "**Yes**" if p["required"] else "No"
                md += f"| `{p['name']}` | `{format_type(p['type'])}` | {req_str} | {p['description']} |\n"
            md += "\n"
            
        # Example Request
        md += "### Example Request (cURL)\n\n"
        md += "```sh\n"
        md += f"curl \"https://api.yourdomain.com{path}\" \\\n"
        md += "  -H \"X-API-Key: sk_test_your_secret_key_here\" \\\n"
        if method.upper() in ["POST", "PUT", "PATCH"]:
            md += "  -H \"Content-Type: application/json\" \\\n"
        if needs_project:
            md += "  -H \"X-Project-ID: your_project_id_here\" \\\n"
            
        if body_props:
            mock_body = {p["name"]: generate_mock_value(p["name"], p["type"]) for p in body_props}
            json_str = json.dumps(mock_body, indent=2)
            # Indent the json string for curl
            json_str_indented = json_str.replace('\n', '\n    ')
            md += f"  -d '{json_str_indented}'"
        else:
            if method.upper() == "GET":
                md = md.rstrip(" \\\n")
        
        md += "\n```\n\n"
        
        # Example Response
        responses = op.get("responses", {})
        success_resp = responses.get("200") or responses.get("201")
        if success_resp:
            resp_content = success_resp.get("content", {}).get("application/json", {}).get("schema", {})
            if resp_content:
                # We can't generate a perfect nested mock easily without a deep parser, but we can try
                # Let's just put a placeholder for now, or build a recursive mocker
                pass
        
        # Just write the section
        sections.append((tags[0], md))

# Group by tags
grouped = {}
for tag, md in sections:
    grouped.setdefault(tag, []).append(md)

final_md = "# API Reference\n\nWelcome to the API reference. Authenticate all requests by including your `X-API-Key` in the request headers.\n\n"

for tag, mds in grouped.items():
    final_md += f"# {tag.capitalize()}\n\n"
    final_md += "---\n\n"
    for md in mds:
        final_md += md
        final_md += "---\n\n"

with open("apikey_doc.md", "w") as f:
    f.write(final_md)

print("Documentation generated.")
