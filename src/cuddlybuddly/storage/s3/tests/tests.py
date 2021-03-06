from datetime import datetime, timedelta
import httplib
import os
from StringIO import StringIO
from time import sleep
import urlparse
from zipfile import ZipFile
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.forms.widgets import Media
from django.template import Context, Template, TemplateSyntaxError
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.encoding import force_unicode
from django.utils.http import urlquote
from cuddlybuddly.storage.s3 import lib
from cuddlybuddly.storage.s3.exceptions import S3Error
from cuddlybuddly.storage.s3.storage import S3Storage
from cuddlybuddly.storage.s3.utils import CloudFrontURLs, create_signed_url


default_storage = S3Storage()


MEDIA_URL = settings.MEDIA_URL
if not MEDIA_URL.endswith('/'):
    MEDIA_URL = MEDIA_URL+'/'

DUMMY_IMAGE = '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYF\nBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoK\nCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAAKAA8DASIA\nAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA\nAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3\nODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm\np6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA\nAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx\nBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK\nU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3\nuLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3PxT8\nZP23oP8AggV47sNP/Z28PS+EF8K+J0HxAf4typqaW/8Aa97mcWH9mn515UR/aeQo+YZwPWP2tPCv\nxF/afv8A4g+AvAfhqWwl8I/tpxwXdz4XiuBNeWw+GVvL9ouzvcbvNvY4sqETbHANu/LN02saXph/\n4N6/GulnToPszeC/Ewa38lfLIOrXhI24xya5n9p+8vNA1v4jXGhXctlJd/tsJ9qe0kMZm/4tfa/f\nK43fcTr/AHF9BQB//9k=\n'.decode('base64')


class UnicodeContentFile(ContentFile):
    """
    A version of ContentFile that never uses cStringIO so that it is always
    unicode compatible.
    """
    def __init__(self, content):
        content = content or ''
        super(ContentFile, self).__init__(StringIO(content))
        self.size = len(content)


class S3StorageTests(TestCase):
    def run_test(self, filename, content='Lorem ipsum dolar sit amet'):
        content = UnicodeContentFile(content)
        filename = default_storage.save(filename, content)
        self.assert_(default_storage.exists(filename))

        self.assertEqual(default_storage.size(filename), content.size)
        now = datetime.now()
        delta = timedelta(minutes=5)
        mtime = default_storage.modified_time(filename)
        self.assert_(mtime > (now - delta))
        self.assert_(mtime < (now + delta))
        file = default_storage.open(filename)
        self.assertEqual(file.size, content.size)
        fileurl = force_unicode(file).replace('\\', '/')
        fileurl = urlquote(fileurl, '/')
        if fileurl.startswith('/'):
            fileurl = fileurl[1:]

        self.assertEqual(
            MEDIA_URL+fileurl,
            default_storage.url(filename)
        )
        file.close()

        default_storage.delete(filename)
        self.assert_(not default_storage.exists(filename))

    def test_absolute_path(self):
        self.run_test('/testsdir/file1.txt')

    def test_relative_path(self):
        self.run_test('testsdir/file2.txt')

    def test_unicode(self):
        self.run_test(u'testsdir/\u00E1\u00E9\u00ED\u00F3\u00FA.txt')

    def test_byte_contents(self):
        self.run_test('testsdir/filebytes.jpg', DUMMY_IMAGE)

    def test_filename_with_spaces(self):
        self.run_test('testsdir/filename with spaces.txt')

    def test_byte_contents_when_closing_file(self):
        filename = u'filebytes\u00A3.jpg'
        file = default_storage.open(filename, 'wb')
        file.write(DUMMY_IMAGE)
        file.close()
        self.assertEqual(default_storage.size(filename), file.size)
        default_storage.delete(filename)
        self.assert_(not default_storage.exists(filename))

    def test_ranged_read(self):
        filename = u'fileranged.jpg'
        file = default_storage.open(filename, 'wb')
        file.write(DUMMY_IMAGE)
        file.close()
        self.assertEqual(default_storage.size(filename), file.size)
        self.assertEqual(len(default_storage.open(filename).read(128)), 128)
        default_storage.delete(filename)
        self.assert_(not default_storage.exists(filename))

    def test_seek(self):
        filename = u'fileseek.jpg'
        file = default_storage.open(filename, 'wb')
        file.write(DUMMY_IMAGE)
        file.close()
        self.assertEqual(default_storage.size(filename), file.size)

        # Recreation of how PIL detects JPEGs.
        file = default_storage.open(filename)
        prefix = file.read(16)
        file.seek(0)
        self.assertEqual(ord(file.read(1)[0]), 255)
        file.close()

        default_storage.delete(filename)
        self.assert_(not default_storage.exists(filename))

    def test_write_to_file(self):
        filename = 'file6.txt'
        default_storage.save(filename, UnicodeContentFile('Lorem ipsum dolor sit amet'))
        self.assert_(default_storage.exists(filename))

        file = default_storage.open(filename, 'w')
        self.assertEqual(file.size, 26)

        file.write('Lorem ipsum')
        file.close()
        self.assertEqual(file.size, 11)

        default_storage.delete(filename)
        self.assert_(not default_storage.exists(filename))

    def run_listdir_test(self, folder):
        content = ('testsdir/file3.txt', 'testsdir/file4.txt',
                 'testsdir/sub/file5.txt')
        for file in content:
            default_storage.save(file, UnicodeContentFile('Lorem ipsum dolor sit amet'))
            self.assert_(default_storage.exists(file))

        dirs, files = default_storage.listdir(folder)
        self.assertEqual(dirs, ['sub'])
        self.assertEqual(files, ['file3.txt', 'file4.txt'])
        if not folder.endswith('/'):
            folder = folder+'/'
        dirs, files = default_storage.listdir(folder+dirs[0])
        self.assertEqual(dirs, [])
        self.assertEqual(files, ['file5.txt'])

        for file in content:
            default_storage.delete(file)
            self.assert_(not default_storage.exists(file))

    def test_listdir_absolute_path(self):
        self.run_listdir_test('/testsdir')

    def test_listdir_relative_path(self):
        self.run_listdir_test('testsdir')

    def test_listdir_ending_slash(self):
        self.run_listdir_test('testsdir/')

    def test_gzip(self):
        ct_backup = getattr(settings, 'CUDDLYBUDDLY_STORAGE_S3_GZIP_CONTENT_TYPES', None)
        settings.CUDDLYBUDDLY_STORAGE_S3_GZIP_CONTENT_TYPES = (
            'text/css',
            'application/javascript',
            'application/x-javascript'
        )

        filename = 'testsdir/filegzip.css'
        file = UnicodeContentFile('Lorem ipsum ' * 512)
        self.assertEqual(file.size, 6144)
        default_storage.save(filename, file)
        self.assertEqual(default_storage.size(filename), 62)

        file2 = default_storage.open(filename)
        self.assertEqual(file2.read(), 'Lorem ipsum ' * 512, 'Failed to read Gzipped content')
        file2.close()

        default_storage.delete(filename)

        if ct_backup is not None:
            settings.CUDDLYBUDDLY_STORAGE_S3_GZIP_CONTENT_TYPES = ct_backup

    def test_exists_on_empty_path(self):
        self.assert_(not default_storage.exists(''))
        self.assert_(not default_storage.exists(None))

    def test_modified_time_on_non_existent_file(self):
        self.assertRaises(
            S3Error,
            default_storage.modified_time,
            'this/file/better/not/exist'
        )

    # Turn off gzip to make file sizes more predictable
    @override_settings(CUDDLYBUDDLY_STORAGE_S3_GZIP_CONTENT_TYPES=())
    def test_chunked_read(self):
        filename = 'testsdir/filechunked.txt'
        file_ = UnicodeContentFile('Lorem ipsum ' * 200)
        self.assertEqual(file_.size, 2400)
        filename = default_storage.save(filename, file_)

        file_ = default_storage.open(filename)
        for i, data in enumerate(file_.chunks(1024)):
            if i == 3:
                length = 0
            elif i == 2:
                length = 352
            else:
                length = 1024
            self.assertEqual(len(unicode(data)), length)

        default_storage.delete(filename)

        # Now for a 0 length read
        filename = 'testsdir/filechunkedzerolength.txt'
        file_ = UnicodeContentFile('')
        self.assertEqual(file_.size, 0)
        filename = default_storage.save(filename, file_)

        file_ = default_storage.open(filename)
        for c in file_.chunks(1024):
            self.assertEqual(len(c), 0)

        default_storage.delete(filename)

    def test_chunked_zipfile_read(self):
        """
        A zip file's central directory is located at the end of the file and
        ZipFile.infolist will try to read chunks from the end before falling
        back to reading the whole file.
        """
        filename = 'testsdir/filechunked.zip'
        file_ = StringIO()
        zip_ = ZipFile(file_, 'a')
        zip_.writestr('test.txt', 'Lorem ipsum ' * 512)
        zip_.close()
        default_storage.save(filename, file_)

        file2 = default_storage.open(filename)
        zip_ = ZipFile(file2)
        self.assertEqual(
            [i.filename for i in zip_.infolist()],
            ['test.txt']
        )
        file2.close()

        default_storage.delete(filename)


class SignedURLTests(TestCase):
    def setUp(self):
        self.conn = lib.AWSAuthConnection(
            settings.AWS_ACCESS_KEY_ID,
            settings.AWS_SECRET_ACCESS_KEY
        )
        self.key = getattr(settings, 'CUDDLYBUDDLY_STORAGE_S3_KEY_PAIR', None)
        settings.CUDDLYBUDDLY_STORAGE_S3_KEY_PAIR = ('PK12345EXAMPLE',
"""-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDA7ki9gI/lRygIoOjV1yymgx6FYFlzJ+z1ATMaLo57nL57AavW
hb68HYY8EA0GJU9xQdMVaHBogF3eiCWYXSUZCWM/+M5+ZcdQraRRScucmn6g4EvY
2K4W2pxbqH8vmUikPxir41EeBPLjMOzKvbzzQy9e/zzIQVREKSp/7y1mywIDAQAB
AoGABc7mp7XYHynuPZxChjWNJZIq+A73gm0ASDv6At7F8Vi9r0xUlQe/v0AQS3yc
N8QlyR4XMbzMLYk3yjxFDXo4ZKQtOGzLGteCU2srANiLv26/imXA8FVidZftTAtL
viWQZBVPTeYIA69ATUYPEq0a5u5wjGyUOij9OWyuy01mbPkCQQDluYoNpPOekQ0Z
WrPgJ5rxc8f6zG37ZVoDBiexqtVShIF5W3xYuWhW5kYb0hliYfkq15cS7t9m95h3
1QJf/xI/AkEA1v9l/WN1a1N3rOK4VGoCokx7kR2SyTMSbZgF9IWJNOugR/WZw7HT
njipO3c9dy1Ms9pUKwUF46d7049ck8HwdQJARgrSKuLWXMyBH+/l1Dx/I4tXuAJI
rlPyo+VmiOc7b5NzHptkSHEPfR9s1OK0VqjknclqCJ3Ig86OMEtEFBzjZQJBAKYz
470hcPkaGk7tKYAgP48FvxRsnzeooptURW5E+M+PQ2W9iDPPOX9739+Xi02hGEWF
B0IGbQoTRFdE4VVcPK0CQQCeS84lODlC0Y2BZv2JxW3Osv/WkUQ4dslfAQl1T303
7uwwr7XTroMv8dIFQIPreoPhRKmd/SbJzbiKfS/4QDhU
-----END RSA PRIVATE KEY-----""")
        self.media_url = settings.MEDIA_URL
        default_storage.base_url = settings.MEDIA_URL = CloudFrontURLs(
            'http://%s.s3.amazonaws.com/' % settings.AWS_STORAGE_BUCKET_NAME,
            patterns={'^horizon.jpg': 'http://d604721fxaaqy9.cloudfront.net'}
        )

    def tearDown(self):
        if self.key is not None:
            settings.CUDDLYBUDDLY_STORAGE_S3_KEY_PAIR = self.key
        default_storage.base_url = settings.MEDIA_URL = self.media_url

    def get_url(self, url):
        url = urlparse.urlparse(url)
        if url.scheme == 'https':
            conn = httplib.HTTPSConnection(url.netloc)
        else:
            conn = httplib.HTTPConnection(url.netloc)
        path = url.path
        if url.query:
            path = path+'?'+url.query
        conn.request('GET', path)
        return conn.getresponse()

    def run_test_signed_url(self, filename):
        response = self.conn.put(
            settings.AWS_STORAGE_BUCKET_NAME,
            filename,
            'Lorem ipsum dolor sit amet.',
            {'x-amz-acl': 'private'}
        )
        self.assertEquals(response.http_response.status, 200, 'put with a string argument')
        response = self.get_url(default_storage.url(filename))
        self.assertEqual(response.status, 403)

        signed_url = create_signed_url(filename, expires=5, secure=True)
        response = self.get_url(signed_url)
        self.assertEqual(
            response.status,
            200,
            'If this is failing, try resyncing your computer\'s clock.'
        )
        sleep(6)
        response = self.get_url(signed_url)
        self.assertEqual(
            response.status,
            403,
            'If this is failing, try resyncing your computer\'s clock.'
        )

        default_storage.delete(filename)
        return signed_url

    def test_signed_url(self):
        self.run_test_signed_url('testprivatefile.txt')

    def test_signed_url_with_spaces(self):
        filename = 'test private file with spaces.txt'
        signed_url = self.run_test_signed_url('test private file with spaces.txt')
        self.assert_(filename.replace(' ', '+') not in signed_url)
        self.assert_(filename.replace(' ', '%20') in signed_url)

    def test_signed_url_with_unicode(self):
        self.run_test_signed_url(u'testprivatefile\u00E1\u00E9\u00ED\u00F3\u00FA.txt')

    def test_signed_url_in_subdir(self):
        self.run_test_signed_url('testdirs/testprivatefile.txt')

    def test_signed_url_in_subdir_with_unicode(self):
        self.run_test_signed_url(u'testdirs/testprivatefile\u00E1\u00E9\u00ED\u00F3\u00FA.txt')

    def test_signed_url_missing_file(self):
        signed_url = create_signed_url('testprivatemissing.txt', expires=5, secure=True)
        response = self.get_url(signed_url)
        self.assertEqual(response.status, 404)

    def test_private_cloudfront(self):
        signed_url = create_signed_url('horizon.jpg?large=yes&license=yes', secure=False, private_cloudfront=True, expires_at=1258237200)
        self.assertEqual(
            signed_url,
            'http://d604721fxaaqy9.cloudfront.net/horizon.jpg?large=yes&license=yes&Expires=1258237200&Signature=Nql641NHEUkUaXQHZINK1FZ~SYeUSoBJMxjdgqrzIdzV2gyEXPDNv0pYdWJkflDKJ3xIu7lbwRpSkG98NBlgPi4ZJpRRnVX4kXAJK6tdNx6FucDB7OVqzcxkxHsGFd8VCG1BkC-Afh9~lOCMIYHIaiOB6~5jt9w2EOwi6sIIqrg_&Key-Pair-Id=PK12345EXAMPLE'
        )


class TemplateTagsTests(TestCase):
    def render_template(self, source, context=None):
        if not context:
            context = {}
        context = Context(context)
        source = '{% load s3_tags %}' + source
        return Template(source).render(context)

    def test_bad_values(self):
        tests = (
            '{% s3_media_url %}',
            '{% s3_media_url "a" as %}',
        )
        for test in tests:
            self.assertRaises(TemplateSyntaxError, self.render_template, test)

    def test_good_values(self):
        tests = {
            '{% s3_media_url "test/file.txt" %}':
                'test/file.txt',
            '{% s3_media_url "test/file2.txt" as var %}':
                '',
            '{% s3_media_url "test/file2.txt" as var %}{{ var }}':
                'test/file2.txt',
            '{% s3_media_url file %}':
                ('test/file3.txt', {'file': 'test/file3.txt'}),
            '{% s3_media_url file as var %}{{ var }}':
                ('test/file4.txt', {'file': 'test/file4.txt'}),
            '{% s3_media_url "test/file%20quote.txt" %}':
                'test/file%20quote.txt',
            '{% s3_media_url "test/file quote.txt" %}':
                'test/file%20quote.txt',
            u'{% s3_media_url "test/fil\u00E9.txt" %}':
                'test/fil%C3%A9.txt',
            '{% s3_media_url "test/fil%C3%A9.txt" %}':
                'test/fil%C3%A9.txt',
        }
        for name, val in tests.items():
            if type(val).__name__ == 'str':
                val = (val, None)
            self.assertEqual(self.render_template(name, val[1]),
                             urlparse.urljoin(settings.MEDIA_URL, val[0]) if val[0] else '')


class CommandTests(TestCase):
    def setUp(self):
        self.backup_exclude = getattr(
            settings,
            'CUDDLYBUDDLY_STORAGE_S3_SYNC_EXCLUDE',
            None
        )
        settings.CUDDLYBUDDLY_STORAGE_S3_SYNC_EXCLUDE = ['\.svn$', 'Thumbs\.db$']
        self.folder = 'cbs3testsync'
        self.basepath = os.path.join(settings.MEDIA_ROOT, self.folder)
        if not os.path.exists(self.basepath):
            os.makedirs(self.basepath)
        self.files = {
            'test1.txt': 'Lorem',
            'test2.txt': 'Ipsum',
            'test3.txt': 'Dolor'
        }
        self.exclude_files = {
            '.svn/test4.txt': 'Lorem',
            'Thumbs.db': 'Ipsum'
        }
        self.created_paths = []
        for files in (self.files, self.exclude_files):
            for filename, contents in files.items():
                path = os.path.join(self.basepath, os.path.split(filename)[0])
                if not os.path.exists(path):
                    self.created_paths.append(path)
                    os.makedirs(path)
                fh = open(os.path.join(self.basepath, filename), 'w')
                fh.write(contents)
                fh.close()
        self.created_paths.append(self.basepath)

    def tearDown(self):
        for files in (self.files, self.exclude_files):
            for file in files.keys():
                try:
                    os.remove(os.path.join(self.basepath, file))
                except:
                    pass
        for dir in self.created_paths:
            try:
                os.rmdir(dir)
            except:
                pass
        if self.backup_exclude is not None:
            settings.CUDDLYBUDDLY_STORAGE_S3_SYNC_EXCLUDE = self.backup_exclude

    def test_sync(self):
        for file in self.files.keys():
            self.assert_(not default_storage.exists(
                os.path.join(self.folder, file))
            )
        call_command(
            'cb_s3_sync_media',
            verbosity=0,
            dir=self.basepath,
            prefix=self.folder
        )
        for file in self.files.keys():
            self.assert_(default_storage.exists(
                os.path.join(self.folder, file))
            )
        for file in self.exclude_files.keys():
            self.assert_(not default_storage.exists(
                os.path.join(self.folder, file))
            )

        modified_times = {}
        for file in self.files.keys():
            modified_times[file] = default_storage.modified_time(
                os.path.join(self.folder, file)
            )

        call_command(
            'cb_s3_sync_media',
            verbosity=0,
            dir=self.basepath,
            prefix=self.folder
        )
        for file in self.files.keys():
            self.assertEqual(
                modified_times[file],
                default_storage.modified_time(os.path.join(self.folder, file)),
                'If this is failing, try resyncing your computer\'s clock.'
            )

        call_command(
            'cb_s3_sync_media',
            verbosity=0,
            dir=self.basepath,
            prefix=self.folder,
            force=True
        )
        for file in self.files.keys():
            self.assert_(
                modified_times[file] < \
                default_storage.modified_time(os.path.join(self.folder, file))
            )

        for file in self.files.keys():
            default_storage.delete(os.path.join(self.folder, file))


class MediaMonkeyPatchTest(TestCase):
    def test_media_monkey_patch(self):
        media = Media()
        media.add_js((
            '/admin/test1.js',
            'admin/test2.js',
            'http://example.com/admin/test3.js',
            '//example.com/admin/test3.js'
        ))
        media.add_css({
            'all': (
                '/admin/test1.css',
                'admin/test2.css',
                'http://example.com/admin/test2.css',
                '//example.com/admin/test2.css'
            )
        })

        no_monkey = """
<link href="/admin/test1.css" type="text/css" media="all" rel="stylesheet" />
<link href="/static/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<link href="http://example.com/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<link href="//example.com/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<script type="text/javascript" src="/admin/test1.js"></script>
<script type="text/javascript" src="/static/admin/test2.js"></script>
<script type="text/javascript" src="http://example.com/admin/test3.js"></script>
<script type="text/javascript" src="//example.com/admin/test3.js"></script>
        """.strip()
        monkey = """
<link href="/admin/test1.css" type="text/css" media="all" rel="stylesheet" />
<link href="http://this.com/static/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<link href="http://example.com/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<link href="//example.com/admin/test2.css" type="text/css" media="all" rel="stylesheet" />
<script type="text/javascript" src="/admin/test1.js"></script>
<script type="text/javascript" src="http://this.com/static/admin/test2.js"></script>
<script type="text/javascript" src="http://example.com/admin/test3.js"></script>
<script type="text/javascript" src="//example.com/admin/test3.js"></script>
        """.strip()

        with self.settings(STATIC_URL='/static/'):
            self.assertEqual(media.render(), no_monkey)

        with self.settings(
            STATIC_URL=CloudFrontURLs('http://notthis.com/', patterns={
                '^admin': 'http://this.com/static/'
            })
        ):
            self.assertEqual(media.render(), monkey)
