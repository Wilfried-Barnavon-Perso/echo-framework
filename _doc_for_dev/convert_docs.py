import json
import sys

def resolve_ref(schema, root):
    """Résout les références $ref internes (ex: #/components/schemas/User)"""
    if '$ref' not in schema:
        return schema
    
    ref_path = schema['$ref'].split('/')
    ref_obj = root
    for part in ref_path:
        if part == '#': continue
        ref_obj = ref_obj.get(part, {})
    return ref_obj

def format_schema(schema, root, indent=0):
    """Formate récursivement un schéma JSON en liste Markdown"""
    out = ""
    prefix = "  " * indent
    
    # Résolution de la référence si nécessaire
    if '$ref' in schema:
        schema = resolve_ref(schema, root)
        
    s_type = schema.get('type', 'object')
    
    if s_type == 'object' and 'properties' in schema:
        for prop, details in schema['properties'].items():
            req = "*(required)*" if prop in schema.get('required', []) else ""
            d_type = details.get('type', 'any')
            if '$ref' in details:
                ref_name = details['$ref'].split('/')[-1]
                d_type = f"[{ref_name}](#model-{ref_name.lower()})"
            
            desc = details.get('description', '').replace('\n', ' ')
            out += f"{prefix}- **{prop}** ({d_type}) {req}: {desc}\n"
            
            # Récursion pour les objets imbriqués
            if 'properties' in details or '$ref' in details:
                 out += format_schema(details, root, indent + 1)
                 
    elif s_type == 'array':
        items = schema.get('items', {})
        out += f"{prefix}- Liste de :\n"
        out += format_schema(items, root, indent + 1)
        
    return out

def generate_md(json_file, md_file):
    with open(json_file, 'r', encoding='utf-8') as f:
        spec = json.load(f)

    with open(md_file, 'w', encoding='utf-8') as f:
        # En-tête
        f.write(f"# Documentation API Open WebUI ({spec.get('info', {}).get('version')})\n\n")
        f.write(f"{spec.get('info', {}).get('description')}\n\n")
        f.write("---\n\n")
        f.write("## Table des Matières\n\n")
        f.write("1. [Endpoints](#endpoints)\n")
        f.write("2. [Modèles de Données (Schemas)](#modèles-de-données)\n\n")
        
        f.write("## Endpoints\n\n")

        # Tri des paths
        paths = sorted(spec.get('paths', {}).items())
        
        for path, methods in paths:
            for method, details in methods.items():
                summary = details.get('summary', 'Pas de résumé')
                desc = details.get('description', '')
                tags = ", ".join(details.get('tags', []))
                
                f.write(f"### {method.upper()} `{path}`\n\n")
                f.write(f"**Tags:** {tags}\n\n")
                f.write(f"**Résumé:** {summary}\n\n")
                if desc:
                    f.write(f"> {desc}\n\n")
                
                # Paramètres
                if 'parameters' in details:
                    f.write("**Paramètres URL / Query :**\n\n")
                    for param in details['parameters']:
                        req = "**Requis**" if param.get('required') else "Optionnel"
                        f.write(f"- `{param['name']}` ({param['in']}) - {req} : {param.get('description', '')}\n")
                    f.write("\n")

                # Body
                if 'requestBody' in details:
                    f.write("**Corps de la requête (Body) :**\n\n")
                    content = details['requestBody'].get('content', {})
                    if 'application/json' in content:
                        schema = content['application/json']['schema']
                        f.write(format_schema(schema, spec))
                    elif 'multipart/form-data' in content:
                        schema = content['multipart/form-data']['schema']
                        f.write(format_schema(schema, spec))
                    f.write("\n")

                f.write("---\n")

        # Modèles (Schemas Pydantic)
        f.write("## Modèles de Données\n\n")
        f.write("Ces objets définissent la structure des réponses et des requêtes.\n\n")
        
        schemas = spec.get('components', {}).get('schemas', {})
        for name, schema in sorted(schemas.items()):
            f.write(f"### <a id='model-{name.lower()}'></a>Object: {name}\n\n")
            if 'description' in schema:
                f.write(f"{schema['description']}\n\n")
            f.write(format_schema(schema, spec))
            f.write("\n---\n")

    print(f"Documentation générée avec succès : {md_file}")

if __name__ == "__main__":
    generate_md('openapi.json', 'OPEN_WEBUI_API_DOCS.md')