# Hotify
Hotify creates hot folders based on a configuration in which predefined shell commands are executed.

# Description
If you run Hotify, all environments will be created in a subfolder defined as `hotify_hot_folder_name`. If you put a file in this hot folder, Hotify will trigger a command specified.

## Configuration file [hotify.yml](hotify.yml)
All settings will be made in the [hotify.yml](hotify.yml). Your hotify environments will be defined in the `hotify_environments` Section
```yml
hotify_environments:
  - name: pdf-ocr-deu
    trigger: ocrmypdf --output-type pdf --deskew --rotate-pages -l deu "{in_file}" "{out_file}"
    in_pattern:
      - "*.pdf"
```
You have to specify a `name` (the name of the hot folder), a `trigger` (the shell command), and `in_pattern` to define the scope of the environment in sense of glob patterns. The trigger could also be a list, if you have a command chain, i.e. consecutive commands. In order to specify input files and output files of the shell commands, three variables which can be used:
* `in_file`: Single input file variable
* `in_files`: Muliple files input. Hotify will wait `hotify_input_multiple_files_delay` seconds before triggering the specified command to make sure that every file has reached the hot folder.
* `out_file`: Output file variable

Please see [hotify.yml](hotify.yml) for some examples.

## CLI
If not specified, Hotify leaves the hot folder like it was. If you set `--clean`, everything will be cleaned up.
```bash
python hotify.py . --clean
```

# License
This project is licensed under the MIT license - see the [LICENSE](LICENSE) file for details.
