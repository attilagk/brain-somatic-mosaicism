import pandas as pd
import numpy as np
import re
import os.path
import functools
import operator
import copy
import itertools


def tsvpath2annotname(tsvpath):
    val = re.sub('.txt', '', os.path.basename(tsvpath))
    return(val)

def read_TXT_per_annotation(tsvpath, indivID, tissue='NeuN_pl',
                            annotname=None, na_values=[], simplecolumns=True):
    '''
    Reads a TXT_per_annotation file of SNPnexus into an indexed DataFrame

    Arguments
    tsvpath: path to the file
    indivID: Individual ID without the CMC_ prefix
    tissue: NeuN_pl|NeuN_mn|muscle
    annotname: the name of annotation; if None it is the basename of tsvpath without the .txt extention
    simplecolumns: True if we want to avoid multilevel type column names (MultiIndex)

    Value: an annot DataFrame
    '''
    if annotname is None:
        annotname = tsvpath2annotname(tsvpath)
    annot = pd.read_csv(tsvpath, sep='\t', na_values=na_values)
    def varid2index(varid):
        s = re.sub('^chr(.+):1$', '\\1', varid)
        val = s.split(':')
        return(val)
    l = [[indivID, tissue] + varid2index(s) for s in annot['Variation ID']]
    a = np.array(l)
    columns = ['Individual ID', 'Tissue', 'CHROM', 'POS', 'Mutation']
    df = pd.DataFrame(a, columns=columns)
    df['POS'] = df['POS'].astype('int64')
    annot.index = pd.MultiIndex.from_frame(df)
    annot = annot.loc[:, ~annot.columns.isin(['Variation ID', 'Chromosome', 'Position'])]
    simpleindex = [annotname + '_' + a for a in annot.columns]
    multiindex = pd.MultiIndex.from_product([[annotname], annot.columns], names=['Source', 'Annotation'])
    annot.columns = simpleindex if simplecolumns else multiindex
    return(annot)

def annotation_duplicates(annot, sep=':'):
    '''
    Takes care of rows with duplicated index that occur e.g with overlapping genes

    Arguments
    annot: a pandas DataFrame with possible duplicates
    sep: the separator for the collapsed list of strings

    Value: a copy of annot without duplicates

    Details:
    A duplicated index means that there are two or more rows for a the
    variant defining that index.  This happens for example with the
    "Overlapped Gene" feature in near_gens.txt annotation file of SNPnexus
    when the variant is in an overlap of multiple genes.  In such cases the
    function collapses the list of gene names into a scalar of "sep" separated
    string of names.

    In general only "object" dtype columns are collapsed into a "sep"
    separated string.  For other dtypes simply the first point of the
    duplicates is used and the rest of the points are discarded.
    '''
    # return annot unchanged unless its index has duplicates
    if not any(annot.index.duplicated()):
        return(annot)
    # get the set of index values (variants)
    A = set(annot.index)
    # do something to the row(s) marked by a variant
    def do_var(var):
        lines = annot.loc[[var]].copy()
        # if it's just a single row return it unchanged
        if len(lines) == 1:
            return(lines)
        # otherwise collapse the multiple rows into a single row
        else:
            line = lines.iloc[[0]].copy()
            for col in annot.columns:
                if lines[col].dtype == 'object':
                    line[col] = sep.join(list(lines[col]))
            return(line)
    l = [do_var(a) for a in A]
    val = pd.concat(l, axis=0)
    return(val)

def get_multi_annotations(annotlist,
                          vcflistpath='/big/results/bsm/calls/filtered-vcfs.tsv',
                          annotdirpath='/home/attila/projects/bsm/results/2020-09-07-annotations',
                          na_values=[], simplecolumns=True):
    vcflist = pd.read_csv(vcflistpath, sep='\t', names=['sample', 'file'], index_col='sample')
    samplestr = '((MSSM|PITT)_[0-9]+)_(NeuN_pl|NeuN_mn|muscle)'
    def sample2indivID(sample):
        return(re.sub(samplestr, 'CMC_\\1', sample))
    def sample2tissue(sample):
        return(re.sub(samplestr, '\\3', sample))
    def get_annot(sample, annotyp):
        sampledir = annotdirpath + os.path.sep + sample
        tsvpath = sampledir + os.path.sep + annotyp + '.txt'
        indivID = sample2indivID(sample)
        tissue = sample2tissue(sample)
        na_val = na_values[annotyp] if annotyp in na_values.keys() else []
        try:
            annot = read_TXT_per_annotation(tsvpath, indivID, tissue,
                                            simplecolumns=simplecolumns, na_values=na_val)
            annot = annotation_duplicates(annot, sep=':')
        except ValueError:
            annot = None
        return(annot)
    def do_annotyp(annotyp):
        a = pd.concat([get_annot(s, annotyp) for s in vcflist.index], axis=0)
        return(a)
    annot = pd.concat([do_annotyp(a) for a in annotlist], axis=1)
    return(annot)

def extended_columns(columns, cols2insert, suffix='_bin'):
    def helper(c):
        val = [c, c + suffix] if c in cols2insert else [c]
        return(val)
    l = [helper(c) for c in columns]
    extcolumns = functools.reduce(operator.concat, l)
    return(extcolumns)

def binarize_cols(cols2binarize, annot, calls, suffix='_bin', do_categ=False):
    '''
    Binarize the selected columns of annot and reindex it to match calls

    Arguments
    cols2binarize: list of column names to binarize
    annot: the pandas DataFrame containing cols2binarize
    calls: annot will be reindexed according to this DataFrame
    suffix: of the names of binarized columns

    Value: a copy of annot extended with the binarized columns
    '''
    val = annot.copy()
    columns = extended_columns(columns=annot.columns, cols2insert=cols2binarize, suffix=suffix)
    val = val.reindex(columns=columns)
    val = val.reindex(index=calls.index)
    def do_col(col):
        s = np.int8(val[col].isna())
        val[col + suffix] = pd.Categorical(s) if do_categ else s
    for col in cols2binarize:
        do_col(col)
    return(val)

def regularize_categ_cols(colsdict, annot, calls, nafillval='other'):
    '''
    Regularize categorical columns in annot: map vectors to scalars and fill NAs

    Arguments
    colsdict: a dictonary of column names (keys) and the list of their ordered categories
    annot: the DataFrame to be modified (copy)
    calls: the DataFrame based on our VCF
    nafillval: the value to replace missing values with

    Value: the modified copy of annot

    Details:
    When there are multiple values for a row/variant in a given column
    represented in colsdict then the corresponding order of categories
    determines which value is kept and which are removed.
    '''
    val = annot.copy()
    # deep copy is necessary to prevent mutation of colsdict
    d = copy.deepcopy(colsdict)
    for col, categories in d.items():
        categories += ['other']
        s = val[col]
        def helper(old):
            if old is np.nan:
                return(old)
            if not isinstance(old, str):
                raise ValueError('expected either str or np.nan')
            l = old.split(':')
            for cat in categories:
                if cat in l: return(cat)
            return(old)
        s = [helper(x) for x in s]
        s = pd.Categorical(s, categories=categories, ordered=True)
        s = s.fillna(nafillval)
        val[col] = s
    return(val)

def do_annot(annotlist, calls, cols2process=None):
    annot = get_multi_annotations(annotlist)
    numeric_cols = annot.select_dtypes(np.number).columns
    cols2binarize = [c for c in numeric_cols if c in cols2process]
    annot = binarize_cols(cols2binarize, annot, calls, suffix='_bin')
    return(annot)

def read_annot(csvpath='/home/attila/projects/bsm/results/2020-09-07-annotations/annotated-calls.csv'):
    data = pd.read_csv(csvpath, index_col=['Individual ID', 'Tissue', 'CHROM', 'POS', 'Mutation'])
    return(data)

def vectorize_setvalued(annot, colname, nonestr='None', sepstr=':'):
    '''
    Vectorize a set valued feature/colname modifying annot *in place* (so call this function on a copy of annot!)

    Arguments
    annot: pandas DataFrame returned by do_annot or read by read_annot
    colname: the name of the single column to be vectorized
    sepstr: separator of items e.g ':' in 'protein_coding:antisense'
    nonestr: indicates the lack of 

    Value: annot, modified *in place*
    '''
    ll = [y.split(sepstr) for y in annot[colname]]
    s = set(list(itertools.chain(*ll)))
    s.discard(nonestr)
    prefix = ''
    if any([y in annot.columns for y in s]):
        prefix = colname + '_'
    new_colnames = [prefix + y for y in list(s)]
    old_colnames = list(annot.columns)
    ix = old_colnames.index(colname)
    colnames = old_colnames[:ix] + new_colnames + old_colnames[ix:]
    for col in new_colnames:
        annot[col] = [col in y for y in ll]
    return(annot)

def vectorize_multiple_setvalued(annot, colnamel, nonestrl='None', sepstr=':'):
    '''
    Vectorize multiple set valued feature/colname on *a copy* of annot

    Arguments
    annot: pandas DataFrame returned by do_annot or read by read_annot
    colnamel: list of column names to be vectorized
    nonestrl: lisft of None names
    sepstr: separator of items e.g ':' in 'protein_coding:antisense'

    Value: a modified *copy of* annot
    '''
    data = annot.copy()
    for colname, nonestr in zip(colnamel, nonestrl):
        vectorize_setvalued(data, colname, nonestr=nonestr, sepstr=sepstr)
    return(data)
