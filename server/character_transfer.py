"""Offline character owner migration with Cerimal validation and preserved contents."""
import ctypes
import ctypes.util
import struct
import uuid

from save_wire import MAX_BYTES,cerimal_checksum,xxhash64


def zstd(data,compress=False,dictionary=None):
    try:
        import zstandard
        kwargs={'dict_data':zstandard.ZstdCompressionDict(dictionary)} if dictionary else {}
        return (zstandard.ZstdCompressor(level=3,**kwargs).compress(data) if compress else
                zstandard.ZstdDecompressor(**kwargs).decompress(data,max_output_size=MAX_BYTES))
    except ImportError:
        lib=ctypes.CDLL(ctypes.util.find_library('zstd') or 'libzstd.so.1')
        lib.ZSTD_isError.argtypes=[ctypes.c_size_t];lib.ZSTD_isError.restype=ctypes.c_uint
        if dictionary:
            prefix='C' if compress else 'D'
            create=getattr(lib,'ZSTD_create'+prefix+'Ctx');create.restype=ctypes.c_void_p
            free=getattr(lib,'ZSTD_free'+prefix+'Ctx');free.argtypes=[ctypes.c_void_p]
            context=create()
            try:
                lib.ZSTD_getFrameContentSize.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
                lib.ZSTD_getFrameContentSize.restype=ctypes.c_ulonglong
                size=len(data)+len(data)//128+1024 if compress else lib.ZSTD_getFrameContentSize(data,len(data))
                if not 0<size<=MAX_BYTES:raise ValueError('Unsupported zstd size')
                target=ctypes.create_string_buffer(size)
                function=getattr(lib,'ZSTD_'+('compress_usingDict' if compress else 'decompress_usingDict'))
                function.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]+([ctypes.c_int] if compress else [])
                function.restype=ctypes.c_size_t
                args=[context,target,size,data,len(data),dictionary,len(dictionary)]+([3] if compress else [])
                result=function(*args)
                if lib.ZSTD_isError(result):raise ValueError('Invalid zstd document or dictionary')
                return target.raw[:result]
            finally:free(context)
        if compress:
            lib.ZSTD_compressBound.argtypes=[ctypes.c_size_t];lib.ZSTD_compressBound.restype=ctypes.c_size_t
            size=lib.ZSTD_compressBound(len(data));target=ctypes.create_string_buffer(size)
            lib.ZSTD_compress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int]
            lib.ZSTD_compress.restype=ctypes.c_size_t
            result=lib.ZSTD_compress(target,size,data,len(data),3)
        else:
            lib.ZSTD_getFrameContentSize.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
            lib.ZSTD_getFrameContentSize.restype=ctypes.c_ulonglong
            size=lib.ZSTD_getFrameContentSize(data,len(data))
            if not 0<size<=MAX_BYTES:raise ValueError('Unsupported zstd size')
            target=ctypes.create_string_buffer(size)
            lib.ZSTD_decompress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
            lib.ZSTD_decompress.restype=ctypes.c_size_t
            result=lib.ZSTD_decompress(target,size,data,len(data))
        if lib.ZSTD_isError(result):raise ValueError('Invalid zstd document')
        return target.raw[:result]


def replace_owner(metadata,old_owner,new_owner,dictionary=None):
    """For verified character metadata containing exactly one owning-account GUID.

    Never search/replace compressed bytes: decompress the value section, require
    an unambiguous GUID, preserve the schema, then compress and recalculate XXH64.
    The native game parser must validate migrated real-world metadata before use.
    """
    cerimal_checksum(metadata)
    schema_size,_,compression=struct.unpack_from('<III',metadata,8)
    schema=metadata[28:28+schema_size];payload=metadata[28+schema_size:]
    plain=zstd(payload,dictionary=dictionary) if compression else payload
    old,new=uuid.UUID(old_owner).bytes_le,uuid.UUID(new_owner).bytes_le
    if old==new or plain.count(old)!=1 or new in plain:
        raise ValueError('Ambiguous character owning account')
    changed=plain.replace(old,new)
    payload=zstd(changed,True,dictionary=dictionary) if compression else changed
    sections=schema+payload
    result=metadata[:8]+struct.pack('<IIIQ',schema_size,len(payload),compression,xxhash64(sections))+sections
    cerimal_checksum(result)
    assert (zstd(result[28+schema_size:],dictionary=dictionary) if compression else result[28+schema_size:])==changed
    return result
