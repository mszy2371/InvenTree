"""Management command for backup operations."""

import os
import subprocess

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Run backup or restore via invoke."""

    help = 'Run backup or restore operations'

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            'action',
            type=str,
            choices=['backup', 'restore'],
            help='Action to perform: backup or restore',
        )
        parser.add_argument('--path', type=str, help='Path for backup files')
        parser.add_argument('--db-file', type=str, help='Database file to restore')
        parser.add_argument('--media-file', type=str, help='Media file to restore')
        parser.add_argument(
            '--skip-db', action='store_true', help='Skip database backup/restore'
        )
        parser.add_argument(
            '--skip-media', action='store_true', help='Skip media backup/restore'
        )
        parser.add_argument(
            '--compress',
            action='store_true',
            default=True,
            help='Compress backup files',
        )
        parser.add_argument(
            '--encrypt', action='store_true', help='Encrypt backup files'
        )

    def handle(self, *args, **options):
        """Execute backup/restore command."""
        action = options['action']
        cwd = os.environ.get('INVENTREE_ROOT', '/home/inventree')

        # Build invoke command
        cmd = f'invoke {action}'

        if options['path']:
            cmd += f' --path={options["path"]}'

        if action == 'restore':
            if options['db_file']:
                cmd += f' --db-file={options["db_file"]}'
            if options['media_file']:
                cmd += f' --media-file={options["media_file"]}'

        if options['skip_db']:
            cmd += ' --skip-db'

        if options['skip_media']:
            cmd += ' --skip-media'

        if action == 'backup':
            if options['encrypt']:
                cmd += ' --encrypt'
            if options['compress']:
                cmd += ' --compress'

        try:
            self.stdout.write(f'Running: {cmd}')
            result = subprocess.run(
                cmd,
                check=False,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode == 0:
                self.stdout.write(
                    self.style.SUCCESS(f'✅ {action.title()} completed successfully')
                )
                if result.stdout:
                    self.stdout.write(result.stdout)
            else:
                raise CommandError(f'❌ {action.title()} failed:\n{result.stderr}')
        except subprocess.TimeoutExpired:
            raise CommandError(f'{action.title()} timed out after 1 hour')
        except Exception as e:
            raise CommandError(f'Error running {action}: {e!s}')
