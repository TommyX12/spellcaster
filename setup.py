from setuptools import setup

setup(name='spellcaster',
      version='0.0.1',
      description='Personal automation script manager',
      url='https://github.com/TommyX12/spellcaster',
      author='Tommy Xiang',
      author_email='tommyx058@gmail.com',
      license='MIT',
      packages=['spellcaster'],
      entry_points={
          'console_scripts': [
              'spellcaster = spellcaster.main:main',
          ],
      },
      zip_safe=False)
