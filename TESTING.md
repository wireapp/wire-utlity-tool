# To test your changes:

## Testing the entry point script
NOTE: the default behavior of this script will overwite your .bashrc... if your logged in username is 'nonroot'. If it does so, it stores a date/timestamped backup.

`python3 -m scripts.entrypoint <command>' should let you try out the commands the entry point knows about (status, status-full, versions...). You can just set the variables at the command line, and launch it.

You won't be able to test the interactive mode this way.

## Testing the container
`make build` should build a docker container, assuming you have docker installed.

`make test` should get you the version strings of all of the dependencies in the container (except the es tool, which just gives you it's help.)

